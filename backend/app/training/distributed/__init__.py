"""分布式训练包"""
from __future__ import annotations

from app.training.distributed.strategy import (
    ParallelStrategy,
    ParallelMode,
    DeviceCapability,
    CommBackend,
    create_strategy_from_config,
)
from app.training.distributed.placer import (
    HeterogeneousPlacer,
    PlacementPlan,
    LayerInfo,
    apply_placement,
)
from app.training.distributed.coordinator import (
    DistributedCoordinator,
    DistributedJobRequest,
    LocalDistributedLauncher,
)
from app.training.distributed.worker import main as worker_main
from app.training.distributed.communicator import (
    Communicator,
    NCCLCommunicator,
    GlooCommunicator,
    get_communicator,
    PipelineCommunicator,
    TensorParallelCommunicator,
)
from app.training.distributed.profiler import (
    DistributedProfiler,
    CommunicationProfiler,
    ProfileEvent,
)

__all__ = [
    "ParallelStrategy",
    "ParallelMode",
    "DeviceCapability",
    "CommBackend",
    "create_strategy_from_config",
    "HeterogeneousPlacer",
    "PlacementPlan",
    "LayerInfo",
    "apply_placement",
    "DistributedCoordinator",
    "DistributedJobRequest",
    "LocalDistributedLauncher",
    "worker_main",
    "Communicator",
    "NCCLCommunicator",
    "GlooCommunicator",
    "get_communicator",
    "PipelineCommunicator",
    "TensorParallelCommunicator",
    "DistributedProfiler",
    "CommunicationProfiler",
    "ProfileEvent",
]