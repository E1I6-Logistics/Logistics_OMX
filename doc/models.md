# 모델 다운로드/복사

가중치와 수집 데이터는 Git에 넣지 않습니다. 승인된 공유 저장소 또는 PC에서
`pretrained_model` 폴더 전체를 설치된 LeRobot 폴더 아래로 복사하세요.
선정된 A/B/C/D 모델은 아래 Hugging Face 저장소에서 받을 수 있습니다.

| 품목 | 데이터 | 학습 스텝 | 상대 경로 | 다운로드 |
|---|---:|---:|---|---|
| A | 200 에피소드 | 150,000 | outputs/train/warehouse_all_200_two_cycles_v3_act_250k/checkpoints/150000/pretrained_model | [Hugging Face](https://huggingface.co/Jeonminjun/omx_item_a_200ep_act_150k) |
| B | 100 에피소드 | 150,000 | outputs/train/warehouse_item_b_100_merged_act_200k/checkpoints/150000/pretrained_model | [Hugging Face](https://huggingface.co/Jeonminjun/omx_item_b_100ep_act_150k) |
| C | 160 에피소드 | 170,000 | outputs/train/warehouse_item_c_160_merged_basket_fix_v1_act_200k/checkpoints/170000/pretrained_model | [Hugging Face](https://huggingface.co/Jeonminjun/warehouse_item_c_170k) |
| D | 190 에피소드 | 170,000 | outputs/train/warehouse_item_d_190_merged_act_200k/checkpoints/170000/pretrained_model | [Hugging Face](https://huggingface.co/Jeonminjun/warehouse_item_d_170k) |

폴더에는 model.safetensors, config.json, 전처리/후처리 JSON과 정규화 safetensors가 함께 있어야 합니다.
model.safetensors 하나만 복사하지 마세요. A/B 설정은 `omx_order_models.json`,
C/D 설정은 `omx_cd_order_models.json`에서 관리합니다.

예: PC 터미널에서 새 설치 위치로 B 모델 전송 (주소/계정은 실제 값으로 변경):
```bash
rsync -aP outputs/train/warehouse_item_b_100_merged_act_200k/checkpoints/150000/pretrained_model/ omx4@10.42.0.58:~/il_ws/src/lerobot_omx_release/outputs/train/warehouse_item_b_100_merged_act_200k/checkpoints/150000/pretrained_model/
```

먼저 대상 폴더를 mkdir -p로 생성해야 합니다. 전송 후 같은 명령에 `-rcn --itemize-changes`를 사용해 내용 차이를 확인할 수 있습니다.

C/D는 Hugging Face에서 저장소 전체를 내려받거나 PC의 위 상대 경로에서 OMX02로 복사합니다.
두 모델 모두 170,000스텝 `pretrained_model` 폴더를 사용합니다.
