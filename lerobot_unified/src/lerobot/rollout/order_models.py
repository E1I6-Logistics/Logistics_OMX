"""Select a local ACT policy for a new order while the control loop is idle."""
import json
import logging
from pathlib import Path

from lerobot.configs import PreTrainedConfig
from lerobot.policies import make_pre_post_processors
from lerobot.processor.relative_action_processor import RelativeActionsProcessorStep
from .context import PolicyContext, _load_pretrained_policy
from .inference.sync import SyncInferenceEngine

logger = logging.getLogger(__name__)


class OrderModelRouter:
    def __init__(self, filename, ctx):
        if not isinstance(ctx.policy.inference, SyncInferenceEngine):
            raise ValueError("A/B switching currently requires sync inference")
        if ctx.runtime.cfg.use_torch_compile or ctx.data.dataset is not None:
            raise ValueError("A/B switching requires compilation and rollout recording disabled")
        path = Path(filename).expanduser().resolve()
        self.models = json.loads(path.read_text())
        if not isinstance(self.models, dict) or not self.models:
            raise ValueError("Empty/invalid order model map")
        for item, entry in self.models.items():
            checkpoint = Path(entry['path']).expanduser()
            if not checkpoint.is_absolute():
                checkpoint = path.parent / checkpoint
            entry['path'] = str(checkpoint.resolve())
            if not (checkpoint / 'model.safetensors').is_file() or not entry.get('task'):
                raise ValueError(f"Missing checkpoint or task for {item}: {checkpoint}")
        self.active_item = None

    def select(self, item, ctx):
        item = item.upper()
        if item not in self.models:
            raise ValueError(f"Unknown order item: {item}; expected {list(self.models)}")
        if item == self.active_item:
            return
        entry = self.models[item]
        cfg = PreTrainedConfig.from_pretrained(entry['path'])
        previous = ctx.policy.policy.config
        if cfg.type != 'act' or previous.type != 'act':
            raise ValueError("Only ACT models are supported")
        for attr in ('input_features', 'output_features', 'action_feature_names'):
            if getattr(cfg, attr, None) != getattr(previous, attr, None):
                raise ValueError(f"Model {item} has incompatible {attr}")
        cfg.pretrained_path = Path(entry['path'])
        cfg.device = ctx.runtime.cfg.device
        logger.info("Loading order model %s: %s", item, entry['path'])
        policy = _load_pretrained_policy(cfg).to(cfg.device)
        policy.eval()
        pre, post = make_pre_post_processors(
            policy_cfg=cfg, pretrained_path=entry['path'],
            preprocessor_overrides={
                'device_processor': {'device': cfg.device},
                'rename_observations_processor': {'rename_map': ctx.runtime.cfg.rename_map},
            },
        )
        if any(isinstance(step, RelativeActionsProcessorStep) and step.enabled
               for step in pre.steps):
            raise ValueError("Relative actions are unsupported by sync routing")
        engine = SyncInferenceEngine(
            policy=policy, preprocessor=pre, postprocessor=post,
            dataset_features=ctx.data.dataset_features,
            ordered_action_keys=ctx.data.ordered_action_keys,
            task=entry['task'], device=cfg.device,
            robot_type=ctx.hardware.robot_wrapper.robot_type,
        )
        engine.reset()
        engine.start()
        # Commit only after the replacement has loaded and initialized successfully.
        ctx.policy.inference.stop()
        ctx.policy = PolicyContext(policy, pre, post, engine)
        ctx.runtime.cfg.policy = cfg
        ctx.runtime.cfg.task = entry['task']
        self.active_item = item
        logger.info("Order model ready: item=%s", item)
