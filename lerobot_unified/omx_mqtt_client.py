#!/usr/bin/env python3

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import tempfile
import time
from pathlib import Path

import paho.mqtt.client as mqtt
from lerobot.rollout.workstation_ipc import atomic_json, read_json, valid_ready


# =============================================================
# Argument
# =============================================================

parser = argparse.ArgumentParser()

parser.add_argument("--robot-id", required=True, help="OMX ID (예: omx1)")

parser.add_argument("--broker-ip", required=True, help="FMS MQTT Broker IP")

args = parser.parse_args()

robot_id = args.robot_id
broker_ip = args.broker_ip
broker_port = int(os.environ.get("OMX_BROKER_PORT", "1883"))

PICKUP_ITEMS = {"omx1": ("A", "B"), "omx2": ("C", "D")}
WORKSTATION_IDS = ("omx3", "omx4")

if robot_id not in PICKUP_ITEMS and robot_id not in WORKSTATION_IDS:
    parser.error("robot-id는 omx1, omx2, omx3, omx4 중 하나여야 합니다.")


# =============================================================
# Path
# =============================================================

directory = Path(__file__).resolve().parent

state_path = Path(os.environ.get("OMX_INVENTORY_STATE_PATH", "~/.local/state/lerobot/omx_inventory.json")).expanduser()

new_order_script = directory / "omx_new_order.sh"
omx3_script = directory / "run_e_rollout.sh"
omx4_script = directory / "run_f_rollout.sh"
omx3_policy = os.environ.get(
    "OMX3_POLICY_PATH",
    "outputs/train/warehouse_item_e_132_merged_act_300k/checkpoints/250000/pretrained_model",
)
omx3_port = os.environ.get(
    "OMX3_FOLLOWER_PORT",
    "",
)



# =============================================================
# MQTT / OMX 상태
# =============================================================

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)

job_lock = threading.Lock()

current_job_id = None
current_state = "IDLE"
current_orders = None
from omx_manager.journal import JobJournal
journal = JobJournal(os.environ.get("OMX_JOB_JOURNAL", str(state_path.parent / "jobs.json")))
omx3_process = None
workstation_session = os.environ.get("OMX_WORKSTATION_SESSION", "")
workstation_status = Path(os.environ.get("OMX_WORKSTATION_STATUS_FILE", "/nonexistent/workstation_status.json"))
workstation_command = Path(os.environ.get("OMX_WORKSTATION_CONTROL_FILE", "/nonexistent/workstation_command.json"))


def workstation_state():
    return read_json(workstation_status)


def _workstation_process_alive(status):
    pid = status.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def workstation_ready():
    if not workstation_session:
        return False
    status = workstation_state()
    return valid_ready(status, workstation_session) and _workstation_process_alive(status)


# =============================================================
# MQTT Publish
# =============================================================


# 관련을 발행한다.
def publish(topic, payload):
    message = json.dumps(payload)

    client.publish(topic, message)

    print(f"[SEND] {topic}: {payload}", flush=True)


# 상태를 전송한다.
def send_status():
    publish(f"{robot_id}/status", {"state": current_state})


# 응답을 전송한다.
def send_ack(job_id, accepted=True):
    publish(f"{robot_id}/ack", {"job_id": job_id, "accepted": accepted})


# 진행을 전송한다.
def send_progress(job_id, current, total):
    publish(f"{robot_id}/progress", {"job_id": job_id, "current": current, "total": total})


# 결과를 전송한다.
def send_result(job_id, success, message=""):
    publish(f"{robot_id}/result", {"job_id": job_id, "success": success, "message": message})


# =============================================================
# FMS items -> OMX 주문 변환
# =============================================================


# 주문을 만든다.
def make_orders(items):

    if not isinstance(items, dict):
        raise ValueError("items는 객체 형식이어야 합니다.")

    allowed_items = PICKUP_ITEMS.get(robot_id)

    if allowed_items is None:
        raise ValueError("작업대 하차 동작은 아직 구현되지 않았습니다.")

    if any(item not in allowed_items for item in items):
        raise ValueError(f"{robot_id}에서 처리하지 않는 품목이 있습니다.")

    orders = []

    for item in allowed_items:

        quantity = items.get(item, 0)

        if isinstance(quantity, bool):
            raise ValueError(f"{item} 수량이 올바르지 않습니다.")

        try:
            quantity = int(quantity)

        except (TypeError, ValueError):
            raise ValueError(f"{item} 수량이 올바르지 않습니다.")

        if quantity < 0 or quantity > 8:
            raise ValueError(f"{item} 수량은 0~8이어야 합니다.")

        if quantity > 0:
            orders.append((item, quantity))

    if not orders:
        raise ValueError(f"실행할 {'/'.join(allowed_items)} 주문이 없습니다.")

    return orders


# =============================================================
# 시간 출력
# =============================================================


# 작업 시작 이후 경과 시간을 계산한다.
def elapsed(seconds):

    h, rem = divmod(max(0, int(seconds)), 3600)

    m, s = divmod(rem, 60)

    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"

    return f"{m:02d}:{s:02d}"


# =============================================================
# OMX 실제 주문 실행
# =============================================================


# 주문을 실행한다.
def execute_orders(job_id, orders):

    started = time.monotonic()

    total_quantity = sum(qty for _, qty in orders)

    completed_before = 0

    print("[ORDER] 주문 순서: " + " -> ".join(f"{item} {qty}개" for item, qty in orders), flush=True)

    # Monitoring timeout only; do not confuse this with motor stop.
    per_item_timeout = float(os.environ.get("OMX_PICKUP_ORDER_TIMEOUT_S", "240"))
    if not 0 < per_item_timeout <= 3600:
        raise ValueError("OMX_PICKUP_ORDER_TIMEOUT_S must be within (0,3600]")

    for index, (item, qty) in enumerate(orders, 1):

        # omx2의 물리 모델은 로컬 A/B 명령을 각각 C/D 품목에 사용한다.
        local_item = {"C": "A", "D": "B"}.get(item, item)

        print(f"[ORDER] [{index}/{len(orders)}] " f"{item} {qty}개 등록", flush=True)

        # 기존 OMX 주문 등록 스크립트 사용
        result = subprocess.run(["bash", str(new_order_script), local_item, str(qty)])

        if result.returncode != 0:
            raise RuntimeError(f"{item} 주문 등록 실패")

        if not state_path.exists():
            raise RuntimeError(f"상태 파일이 없습니다: {state_path}")

        accepted = json.loads(state_path.read_text())

        token = accepted["order_started_at_epoch"]

        per_unit = float(os.environ.get("OMX_PICKUP_PER_UNIT_S", str(per_item_timeout)))
        grace = float(os.environ.get("OMX_PICKUP_LOAD_GRACE_S", "60"))
        deadline = time.monotonic() + per_unit * qty + grace
        last_progress = None

        # -----------------------------------------------------
        # OMX inventory 상태 감시
        # -----------------------------------------------------

        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Pickup order timeout: {item} {qty}, {per_item_timeout:.0f}s; motor stop NOT guaranteed")

            # Launcher may stop the rollout while MQTT is still alive.  Fail closed
            # if an explicitly supplied pid is no longer running.
            rollout_pid_text = os.environ.get("OMX_ROLLOUT_PID", "")
            if not rollout_pid_text:
                raise RuntimeError("Pickup rollout PID missing; refusing unmonitored order")
            if rollout_pid_text:
                try:
                    rollout_pid = int(rollout_pid_text)
                    if rollout_pid <= 0:
                        raise ValueError("invalid pid")
                    os.kill(rollout_pid, 0)
                except ProcessLookupError as exc:
                    raise RuntimeError("Pickup rollout process exited") from exc
                except (ValueError, PermissionError) as exc:
                    raise RuntimeError("Pickup rollout pid invalid/inaccessible") from exc

            state = json.loads(state_path.read_text())

            current_order = (state.get("order_item"), state.get("order_quantity"), state.get("order_started_at_epoch"))

            expected_order = (local_item, qty, token)

            if current_order != expected_order:
                raise RuntimeError("주문 상태가 외부에서 변경되었습니다.")

            done = int(state["order_completed"])

            remaining = int(state["remaining"])

            total_completed = completed_before + done

            # 진행 상태가 바뀌었을 때만 progress 전송
            if total_completed != last_progress:

                send_progress(job_id, total_completed, total_quantity)

                print(
                    f"[ORDER] {item} " f"완료={done}/{qty}, " f"전체=" f"{total_completed}/" f"{total_quantity}",
                    flush=True,
                )

                last_progress = total_completed

            # 해당 품목 작업 완료
            if remaining == 0:

                if done != qty:
                    raise RuntimeError(f"{item} 완료 수량 불일치: " f"완료={done}, 주문={qty}")

                print(f"[ORDER] " f"{item} 주문 완료", flush=True)

                completed_before += qty

                break

            time.sleep(0.5)

    print("[ORDER] 전체 주문 완료 | " f"총 소요시간: " f"{elapsed(time.monotonic() - started)}", flush=True)


# OMX3/4: policy, motors and cameras stay connected. No subprocess per command.
def execute_unload(job_id, items):
    if robot_id not in WORKSTATION_IDS:
        raise RuntimeError("Not a workstation")
    if not workstation_ready():
        raise RuntimeError("Workstation not READY; refusing job")
    managed=os.environ.get('OMX_MANAGED_UNLOAD')=='1'
    extra={}
    if managed:
        from omx_manager.workstation import expected_quantity
        extra=dict(expected_count=expected_quantity(items),items=items)
    atomic_json(workstation_command, {"session": workstation_session,
                                     "command": "START", "job_id": str(job_id), "requested_at": time.time(),**extra})
    timeout = float(os.environ.get("OMX_UNLOAD_TIMEOUT_S", "900"))
    deadline = time.monotonic() + timeout
    saw_running = False
    last_progress=None
    while time.monotonic() < deadline:
        status = workstation_state()
        if status.get("session") != workstation_session:
            raise RuntimeError("Workstation session changed during job")
        if not _workstation_process_alive(status):
            raise RuntimeError("Workstation rollout process died during job")
        phase = status.get("phase")
        if phase == "ERROR":
            raise RuntimeError(f"Workstation fault: {status.get('message', '')}")
        if status.get("job_id") == job_id:
            if managed and status.get('estimated_transfers')!=last_progress:
                last_progress=status.get('estimated_transfers',0)
                publish(f'{robot_id}/progress',dict(job_id=job_id,current=last_progress,
                        total=extra['expected_count'] or 0,total_known=extra['expected_count'] is not None,count_is_estimate=True,attempts=status.get('attempts',0),
                        completed_cycles=status.get('completed_cycles',0)))
            if phase == "RUNNING":
                saw_running = True
            if phase == "DONE":
                if status.get("reason") != ('basket_empty_confirmed' if managed else 'home_stillness_after_departure'):
                    raise RuntimeError("Unexpected workstation completion reason")
                if managed and status.get('basket_empty_verified') is not True:
                    raise RuntimeError('Unload completion lacks empty-basket confirmation')
                # Release completion event; next order must wait until READY again.
                atomic_json(workstation_command, {"session": workstation_session,
                                                 "command": "CLEAR", "job_id": str(job_id),
                                                 "requested_at": time.time()})
                break
        time.sleep(0.1)
    else:
        raise TimeoutError(f"Workstation job timeout after {timeout:.0f}s; robot may still be moving")
    for _ in range(100):
        if workstation_ready():
            return status
        s = workstation_state()
        if s.get('phase') == 'ERROR':
            raise RuntimeError(f"Workstation fault on reset: {s.get('message')}")
        time.sleep(0.1)
    raise RuntimeError("Workstation completed but did not return to READY")


# =============================================================
# 실제 작업 Thread
# =============================================================


# 작업을 실행한다.
def run_job(job_id, orders):

    global current_job_id
    global current_state
    global current_orders

    success = False
    result_message = ""

    try:

        current_state = "WORKING"

        send_status()

        if robot_id in WORKSTATION_IDS:
            execute_unload(job_id, orders)
        else:
            execute_orders(job_id, orders)

        success = True

    except Exception as exc:

        print(f"[ORDER] 작업 실패: {exc}", flush=True)

        result_message = str(exc)
        if robot_id in PICKUP_ITEMS:
            abort_path = os.environ.get("OMX_ABORT_FILE", "")
            if abort_path:
                try:
                    # Flag creation is atomic at the filesystem level; no motor commands here.
                    Path(abort_path).touch(exist_ok=True)
                except OSError:
                    print("[SAFETY] Failed to signal rollout abort", flush=True)

    finally:
        done_message = (("Basket empty confirmed; transfer count estimated, physical destination unverified" if os.environ.get('OMX_MANAGED_UNLOAD')=='1'
                         else "Home stillness detected after movement; physical unloading unverified")
                        if robot_id in WORKSTATION_IDS else "Home cycle completed; physical transfer unverified")
        try:
            journal.finish(job_id, success, done_message if success else result_message)
        except Exception as exc:
            success = False
            result_message = f"Journal commit failed: {exc}"
            abort_path = os.environ.get("OMX_ABORT_FILE", "")
            if abort_path: Path(abort_path).touch(exist_ok=True)
        # 작업이 끝난 뒤 상태를 먼저 정리해야 Result 직후의 다음 Command를 받을 수 있다.
        current_job_id = None
        current_orders = None
        current_state = ("IDLE" if success and (robot_id not in WORKSTATION_IDS or workstation_ready()) else "ERROR")

        try:
            send_status()
        finally:
            job_lock.release()

        if success:
            print(f"[ORDER] Job 완료: {job_id}", flush=True)

        done_message = (("Basket empty confirmed; transfer count estimated, physical destination unverified" if os.environ.get('OMX_MANAGED_UNLOAD')=='1'
                         else "Home stillness detected after movement; physical unloading unverified")
                        if robot_id in WORKSTATION_IDS else "Home cycle completed; physical transfer unverified")
        send_result(job_id, success, done_message if success else result_message)


# =============================================================
# MQTT Command 처리
# =============================================================


# 명령 이벤트를 처리한다.
def on_command(data):

    global current_job_id
    global current_state
    global current_orders

    job_id = data.get("job_id")
    items = data.get("items")

    if not isinstance(job_id, str) or not job_id or len(job_id) > 200 or not isinstance(items, dict):

        print("[MQTT] job_id가 없습니다.", flush=True)

        return


    print()
    print("==============================")
    print("COMMAND RECEIVED")
    print("==============================")
    print(f"Job   : {job_id}")
    print(f"Items : {items}")
    print("==============================")

    try:
        prior = journal.lookup(job_id, items)
    except (ValueError, OSError) as exc:
        print(f"[ERROR] Job journal: {exc}", flush=True)
        send_ack(job_id, False)
        return
    if prior and current_job_id != job_id:
        if prior["state"] in ("DONE", "INTERRUPTED"):
            send_ack(job_id, True)
            send_result(job_id, prior.get("success", False), prior.get("message", "Interrupted physical job"))
        else:
            send_ack(job_id, False)
        return

    if robot_id in WORKSTATION_IDS and current_job_id != job_id and not workstation_ready():
        print(f"[MQTT] {robot_id} not READY; rejecting command {job_id}", flush=True)
        send_ack(job_id, False)
        return

    # =========================================================
    # 현재 실행 중인 job과 동일
    # =========================================================

    if current_job_id == job_id:

        try:
            same_orders = (True if robot_id in WORKSTATION_IDS
                           else tuple(make_orders(items)) == current_orders)
        except ValueError:
            same_orders = False

        if not same_orders:
            print(f"[MQTT] 같은 Job ID에 다른 주문이 들어왔습니다: {job_id}", flush=True)
            send_ack(job_id, False)
            return

        print(f"[MQTT] 이미 실행 중인 Job: " f"{job_id}", flush=True)

        send_ack(job_id, True)

        return

    # =========================================================
    # 다른 작업 진행 중
    # =========================================================

    if current_state == "ERROR":
        print(f"[SAFETY] ERROR state: reject job={job_id}", flush=True)
        send_ack(job_id, False)
        return

    if not job_lock.acquire(blocking=False):

        print(f"[MQTT] OMX BUSY - " f"주문 거절: {job_id}", flush=True)

        send_ack(job_id, False)

        return

    # =========================================================
    # 주문 검증
    # =========================================================

    try:

        if robot_id in WORKSTATION_IDS and os.environ.get('OMX_MANAGED_UNLOAD')=='1':
            from omx_manager.workstation import expected_quantity
            expected_quantity(items)
            orders=items.copy()
        else:
            orders = ("TRIGGER",) if robot_id in WORKSTATION_IDS else make_orders(items)

    except Exception as exc:

        print(f"[MQTT] 주문 검증 실패: " f"{exc}", flush=True)

        send_ack(job_id, False)

        send_result(job_id, False, str(exc))

        job_lock.release()

        return

    # =========================================================
    # 주문 접수
    # =========================================================

    try:
        journal.begin(job_id, items)
    except Exception:
        job_lock.release()
        raise
    current_job_id = job_id
    current_orders = tuple(orders)
    current_state = "WORKING"

    send_ack(job_id, True)

    # MQTT callback이 막히지 않도록
    # 실제 OMX 작업은 별도 Thread에서 실행
    worker = threading.Thread(target=run_job, args=(job_id, orders), daemon=True)

    worker.start()


# =============================================================
# MQTT Callback
# =============================================================


# 연결 이벤트를 처리한다.
def on_connect(client, userdata, flags, rc):
    global current_state
    print(f"[MQTT] Connected: {rc}", flush=True)
    if robot_id in WORKSTATION_IDS and current_job_id is None:
        current_state = "IDLE" if workstation_ready() else "ERROR"

    topic = f"{robot_id}/command"

    client.subscribe(topic)

    print(f"[MQTT] Subscribe: " f"{topic}", flush=True)

    send_status()


# disconnect 이벤트를 처리한다.
def on_disconnect(client, userdata, rc):

    print(f"[MQTT] Disconnected: {rc}", flush=True)


# message 이벤트를 처리한다.
def on_message(client, userdata, msg):

    try:

        data = json.loads(msg.payload.decode())

    except Exception as exc:

        print(f"[MQTT] JSON Error: {exc}", flush=True)

        return

    if msg.topic == (f"{robot_id}/command"):
        if msg.retain:
            print("[MQTT] Retained command ignored", flush=True)
            return
        if not isinstance(data, dict): return
        on_command(data)


client.on_connect = on_connect
client.on_disconnect = on_disconnect
client.on_message = on_message


# =============================================================
# 종료 시 OMX3의 자식 프로세스도 종료 (로봇팔 안전정지는 별도 보장 필요)
# =============================================================


def stop_omx3_process(signum, frame):
    if robot_id in WORKSTATION_IDS and omx3_process is not None:
        try:
            os.killpg(omx3_process.pid, signal.SIGINT)
            try:
                omx3_process.wait(timeout=45)
            except subprocess.TimeoutExpired:
                print("[WARN] 하차 Rollout 종료 지연: 로봇팔 정지 상태를 직접 확인하세요", flush=True)
        except ProcessLookupError:
            pass
    raise SystemExit(128 + signum)


# =============================================================
# Main
# =============================================================


# 명령행 프로그램의 실행 절차를 시작한다.
def main():
    if robot_id in WORKSTATION_IDS and not workstation_ready():
        raise SystemExit("[ERROR] Persistent workstation not READY. Use omx_start, not direct MQTT client")
    signal.signal(signal.SIGTERM, stop_omx3_process)
    signal.signal(signal.SIGINT, stop_omx3_process)

    print("==============================")
    print("OMX MQTT Client")
    print("==============================")
    print(f"OMX ID      : {robot_id}")
    print(f"Broker      : {broker_ip}")
    print(f"Broker Port : {broker_port}")
    print(f"Order Script: {new_order_script}")
    print(f"State File  : {state_path}")
    print("==============================")

    if robot_id in PICKUP_ITEMS and not new_order_script.exists():
        raise FileNotFoundError(f"omx_new_order.sh가 없습니다: {new_order_script}")
    if robot_id in WORKSTATION_IDS:
        script = omx4_script if robot_id == "omx4" else omx3_script
        if not script.is_file():
            raise FileNotFoundError(f"하차 실행 파일이 없습니다: {script}")

    client.connect(broker_ip, broker_port, 60)

    client.loop_start()

    try:

        while True:

            # FMS 연결 확인용 heartbeat
            send_status()

            time.sleep(2)

    except KeyboardInterrupt:

        print("\n[MQTT] Stopping...", flush=True)

    finally:

        client.loop_stop()

        client.disconnect()


if __name__ == "__main__":
    main()
