# Logistics_OMX — preloaded workstation rollout (candidate, 2026-10-23)

## Purpose and scope

- **OMX1/OMX2 pickup stays as-is**: A→original B checkpoint, B→original A checkpoint; C/D have their own models. Inventory/Home semantics unchanged.
- **OMX3/OMX4**: start LeRobot + policy + cameras + follower once at `omx_start`, wait with **no commanded motion**, execute one job on MQTT signal, wait for Home departure→return and 15 s home stillness, and return to READY without reloading the policy.
- F checkpoint remains **250000 wrist_only** (`omx_f_test_follower_arm` calibration) until the model and camera setup are deliberately upgraded.
- **Not a motion accuracy fix**: ACT remains synchronous on CPU, originally measuring about 0.5–0.6 Hz; camera placement, training distribution, hardware control, and inference timing are not altered.
- Runtime `.env`, pretrained weights, calibration JSON, old source and Python venv are **not included and are never overwritten** by these scripts.

## Safest deployment

Use a new branch of your Windows/PC GitHub checkout (`E1I6-Logistics/Logistics_OMX`), with no currently active jobs. Do not blindly overwrite a production checkout while its `omx_start` is running.

1. Back up the **entire code checkout**, then extract the update ZIP into a *new Git working copy*, or copy only the changed files. The `full_source_candidate` ZIP is a historical unified source snapshot plus the new changes; the **overlay ZIP** is preferable for updating **current GitHub main** (preserves recently committed `datasets/` and any newer sources).
2. Run `bash scripts/check_omx_release.sh`, inspect `git diff`, commit to a test branch, push to GitHub. **Do not replace main before physical testing**.
3. On **OMX3 only first**, safely stop `omx_start`, back up the current checkout, and switch the existing `~/Logistics_OMX` working tree to the test branch (the installer intentionally requires this exact path). Do **not** change branches while Rollout is active. Preserve `~/venv/.venv`, `~/il_ws/src/lerobot/outputs`, and `~/.cache/huggingface/lerobot/calibration/`. After OMX3 validation, repeat on OMX4; OMX1/2 later.
4. `bash scripts/install_omx.sh --robot-id omxN --broker-ip 192.168.0.16` retains each device's existing `profile.env`; `bash scripts/preflight_omx.sh` verifies config without motor motion.
5. Ensure the follower is **physically in the intended Home pose**, the work area is clear, and an emergency stop is accessible before launching. The initial observed robot pose becomes the Home reference, as in the original script.
6. On OMX3 or OMX4 only, run `omx_start`. Expect `[WORKSTATION] READY` (policy loaded, cameras connected; no motor commands), followed by MQTT `IDLE`. Check `state/omx3_workstation_status.json` or `state/omx4_workstation_status.json`.
7. Test one low-risk FMS job. Expect `READY → RUNNING → DONE → READY`, then MQTT `result`. Stopping after Home stillness **does not prove a successful physical transfer**. If it grips air or stalls, stop and investigate cameras, actions and joint states rather than counting it as a success.
8. Repeated orders should reuse the same rollout PID and model; time from MQTT order to physical movement is still subject to CPU ACT inference latency.

## Operational notes

- Git repo main from 2026-10-23 already contains the `datasets/` source. An earlier local unified v1 ZIP did **not**; therefore always prefer the **overlay ZIP** on the latest GitHub clone. If starting from the included historical full tree, run `bash scripts/complete_unified_source.sh` on the Pi where the original `~/il_ws/src/lerobot/src/lerobot/` is available. This `rsync --ignore-existing` never overwrites unified files.
- `OMX_CPU_THREADS` defaults to online cores, max 4. It is a starting configuration, **not proven optimal for every policy**; profile.env overrides it. There is no overclocking, forced affinity, real-time priority, pip upgrade or automatic torch.compile.
- MQTT protocol/topic payloads are unchanged. Workstation status/commands use session-scoped, atomically written JSON in repo `state/` and are **not for manually sending joint commands**.
- On boot the MQTT client starts **only after** workstation READY. New jobs are rejected when not ready. If a job hangs, timeout reports error but cannot safely guarantee motor torque removal; an operator must intervene. Always keep a reachable physical emergency stop.
- For process and thermal monitoring: `bash scripts/omx_monitor.sh 5 60`. For syntax/static checks: `bash scripts/check_omx_release.sh`.
- Do not alter `stuck_basket_mask_enabled`, Home thresholds, image resolutions, model keys or actuator calibration as part of this update. Investigate OMX2 completion and OMX3 empty-air grasps separately.
- The `/tmp/omx_omxN.lock` single-process guard remains active. Stop existing jobs before changing versions; do **not** `git clean -fdx`.

## IPC states (OMX3/4 only)

`state/omxN_workstation_status.json` is written only by the active rollout and has a unique `session` per run. `state/omxN_workstation_command.json` is written only by the MQTT client. Valid commands are `START` while READY and `CLEAR` after DONE; stale or mismatched session commands are rejected. The main backend watches both rollout and MQTT subprocesses and shuts them down on failure.

## Validation status

**Static checks only**: shell parser, Python compilation/AST, file IPC unit tests, protocol transition mock tests. There has been **no Raspberry Pi, camera, motor, physical pick/unload, MQTT integration or measured ACT benchmark test** for this candidate. Roll out incrementally with a rollback branch.
