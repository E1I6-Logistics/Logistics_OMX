"""Interactive registration front end; motion always requires typing MOVE."""
def run(path):
    from .cli import invoke
    while True:
        print('\n사이클/하차/복귀 설정\n1 새 설정 항목 추가\n2 모델을 돌리며 사이클 복귀 영역 보정\n3 바구니 좌표 영상 저장\n4 빈 바구니 기준 저장\n5 바구니 실시간 판정 시험\n6 복귀 경유점 추가\n7 홈에서 복귀 경로 왕복 실물 시험\n8 등록 검사\n0 돌아가기')
        choice=input('> ').strip()
        if choice=='0':return
        try:
            if choice=='1':invoke(path,'setup-workflow')
            elif choice=='2':
                item=input('품목 A~F: ').strip().upper()
                if input('모델이 실제로 움직입니다. 작업 공간 비운 뒤 MOVE 입력: ').strip()=='MOVE':invoke(path,'cycle-calibrate',item=item,confirm_motion=True)
            elif choice=='3':invoke(path,'basket-snapshot')
            elif choice=='4':
                if input('빈 바구니·조명·홈 자세 확인 후 EMPTY 입력: ').strip()=='EMPTY':invoke(path,'basket-capture',confirm_empty=True)
            elif choice=='5':invoke(path,'basket-watch')
            elif choice=='6':invoke(path,'route-capture',name=input('경로 이름: ').strip())
            elif choice=='7':
                name=input('경로 이름: ').strip()
                if input('홈에서 역방향 이동 후 홈 복귀합니다. 전체 경로 비움·팔 토크 확인 후 MOVE 입력: ').strip()=='MOVE':
                    invoke(path,'route-test',name=name,confirm_motion=True,round_trip=True)
            elif choice=='8':invoke(path,'workflow-check')
        except KeyboardInterrupt:print('등록/시험 중단: 경로 승인은 성공 때만 저장됩니다.')
        except Exception as exc:print('[ERROR]',exc)
