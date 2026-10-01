# 모델 다운로드/복사

가중치와 수집 데이터는 Git에 넣지 않습니다. 승인된 공유 저장소 또는 PC에서
`pretrained_model` 폴더 전체를 설치된 LeRobot 폴더 아래로 복사하세요.
현재 공유용 다운로드 URL은 정해지지 않았습니다. 공개 Hub에 업로드됐다고 가정하지 않습니다.

| 품목 | 데이터 | 학습 스텝 | 상대 경로 |
|---|---|---|---|
| A | 200 에피소드 | 150,000 | outputs/train/warehouse_all_200_two_cycles_v3_act_250k/checkpoints/150000/pretrained_model |
| B | 100 에피소드 | 150,000 | outputs/train/warehouse_item_b_100_merged_act_200k/checkpoints/150000/pretrained_model |

폴더에는 model.safetensors, config.json, 전처리/후처리 JSON과 정규화 safetensors가 함께 있어야 합니다.
model.safetensors 하나만 복사하지 마세요. 모델 설정은 `omx_order_models.json`에서 관리합니다.

예: PC 터미널에서 새 설치 위치로 B 모델 전송 (주소/계정은 실제 값으로 변경):
```bash
rsync -aP outputs/train/warehouse_item_b_100_merged_act_200k/checkpoints/150000/pretrained_model/ omx4@10.42.0.58:~/il_ws/src/lerobot_omx_release/outputs/train/warehouse_item_b_100_merged_act_200k/checkpoints/150000/pretrained_model/
```

먼저 대상 폴더를 mkdir -p로 생성해야 합니다. 전송 후 같은 명령에 `-rcn --itemize-changes`를 사용해 내용 차이를 확인할 수 있습니다.
