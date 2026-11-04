"""分布式策略与异构放置单元测试。

对应验收标准：
- 同构数据并行：微批次均分
- 异构感知：算力强的设备分得更多微批次（加权分配）
- 设备能力模型：GPU/CPU 正确识别
- 并行策略：dp/pp/tp 组构建正确
- 异构放置：显存约束优先、层到设备映射完整
"""
from __future__ import annotations

import pytest
import torch


def _cap(worker_id: str, rank: int, vram_mb: int, tflops: float, device_type: str = "cpu"):
    """构造测试用设备能力（默认 CPU 设备，避免测试依赖 CUDA）"""
    from app.training.distributed.strategy import DeviceCapability

    device = torch.device("cpu")
    return DeviceCapability(
        worker_id=worker_id,
        rank=rank,
        device=device,
        device_type=device_type,
        device_name=f"test-{device_type}",
        vram_mb=vram_mb,
        compute_tflops=tflops,
        memory_bandwidth_gbps=500.0,
        interconnect="pcie",
    )


class TestParallelStrategy:
    """并行策略基础行为"""

    def test_world_size_dp(self):
        from app.training.distributed.strategy import ParallelStrategy, ParallelMode

        s = ParallelStrategy(mode=ParallelMode.DATA_PARALLEL, dp_degree=4)
        assert s.world_size == 4

    def test_world_size_hybrid(self):
        """混合并行 world_size = dp * pp * tp"""
        from app.training.distributed.strategy import ParallelStrategy, ParallelMode

        s = ParallelStrategy(mode=ParallelMode.HYBRID, dp_degree=2, pp_degree=2, tp_degree=2)
        assert s.world_size == 8

    def test_micro_batch_allocation_uniform_for_dp(self):
        """数据并行：全局批均分到各 rank"""
        from app.training.distributed.strategy import ParallelStrategy, ParallelMode

        s = ParallelStrategy(mode=ParallelMode.DATA_PARALLEL, dp_degree=4)
        alloc = s.compute_micro_batch_allocation(64)
        assert alloc == {0: 16, 1: 16, 2: 16, 3: 16}

    def test_micro_batch_allocation_weighted_for_heterogeneous(self):
        """异构模式：算力 2:1 的两台设备，批次按 2:1 加权"""
        from app.training.distributed.strategy import ParallelStrategy, ParallelMode

        caps = [
            _cap("w-fast", 0, vram_mb=24000, tflops=100.0),
            _cap("w-slow", 1, vram_mb=8000, tflops=50.0),
        ]
        s = ParallelStrategy(mode=ParallelMode.HETEROGENEOUS, dp_degree=2, device_capabilities=caps)
        alloc = s.compute_micro_batch_allocation(90)
        # 100/150 -> 60，50/150 -> 30（最后一台兜底承接余量）
        assert alloc[0] + alloc[1] == 90
        assert alloc[0] > alloc[1]  # 快设备分得更多


class TestCreateStrategyFromConfig:
    """从配置构建策略"""

    def test_dp_strategy_builds_dp_groups(self):
        from app.training.distributed.strategy import create_strategy_from_config, ParallelMode

        caps = [_cap(f"w{i}", i, 8000, 50.0) for i in range(4)]
        s = create_strategy_from_config({"mode": "dp", "dp_degree": 4}, caps)
        assert s.mode == ParallelMode.DATA_PARALLEL
        assert s.world_size == 4
        # 4 卡 1 组
        assert any(len(g) == 4 for g in s.dp_groups)

    def test_pp_strategy_builds_stages(self):
        from app.training.distributed.strategy import create_strategy_from_config, ParallelMode

        caps = [_cap(f"w{i}", i, 8000, 50.0) for i in range(4)]
        s = create_strategy_from_config({"mode": "pp", "pp_degree": 2}, caps)
        assert s.mode == ParallelMode.PIPELINE_PARALLEL
        assert len(s.pp_stages) == 2

    def test_string_mode_parsed(self):
        """mode 字符串自动转枚举"""
        from app.training.distributed.strategy import create_strategy_from_config

        s = create_strategy_from_config({"mode": "dp"}, [_cap("w0", 0, 8000, 50.0)])
        assert s.world_size == 1


class TestDeviceCapability:
    """设备能力模型"""

    def test_gpu_detection(self):
        cap = _cap("gpu-0", 0, 24000, 100.0, device_type="cuda")
        assert cap.is_gpu is True

    def test_cpu_detection(self):
        cap = _cap("cpu-0", 0, 0, 10.0, device_type="cpu")
        assert cap.is_gpu is False

    def test_normalized_memory_in_gb(self):
        cap = _cap("gpu-0", 0, 24000, 100.0)
        assert cap.normalized_memory == pytest.approx(24000 / 1024)


class TestHeterogeneousPlacer:
    """异构感知放置器"""

    @pytest.fixture
    def simple_model(self):
        """小型 MLP：3 个 Linear 层"""
        return torch.nn.Sequential(
            torch.nn.Linear(16, 32),
            torch.nn.ReLU(),
            torch.nn.Linear(32, 32),
            torch.nn.ReLU(),
            torch.nn.Linear(32, 4),
        )

    def test_placement_covers_all_layers(self, simple_model):
        """所有叶子层都有设备映射"""
        from app.training.distributed.placer import HeterogeneousPlacer
        from app.training.distributed.strategy import ParallelStrategy, ParallelMode

        caps = [
            _cap("big-gpu", 0, vram_mb=80000, tflops=300.0),
            _cap("small-gpu", 1, vram_mb=8000, tflops=60.0),
        ]
        strategy = ParallelStrategy(mode=ParallelMode.HETEROGENEOUS, dp_degree=2, device_capabilities=caps)
        placer = HeterogeneousPlacer(strategy)
        plan = placer.place_model(simple_model, input_shape=(1, 16))

        # 每个叶子模块都被分配
        leaf_names = [n for n, m in simple_model.named_modules() if len(list(m.children())) == 0]
        for name in leaf_names:
            assert name in plan.layer_to_device, f"层 {name} 未分配设备"

    def test_embedding_prefers_largest_vram(self, simple_model):
        """显存敏感的大层应分配到最大显存设备"""
        from app.training.distributed.placer import HeterogeneousPlacer
        from app.training.distributed.strategy import ParallelStrategy, ParallelMode

        caps = [
            _cap("big-gpu", 0, vram_mb=80000, tflops=300.0),
            _cap("small-gpu", 1, vram_mb=4000, tflops=60.0),
        ]
        strategy = ParallelStrategy(mode=ParallelMode.HETEROGENEOUS, dp_degree=2, device_capabilities=caps)
        placer = HeterogeneousPlacer(strategy)
        plan = placer.place_model(simple_model, input_shape=(1, 16))

        # 至少一层被分配到 rank 0（大显存）
        assert 0 in plan.layer_to_rank.values()

    def test_memory_usage_tracked(self, simple_model):
        """放置后记录了每设备显存占用"""
        from app.training.distributed.placer import HeterogeneousPlacer
        from app.training.distributed.strategy import ParallelStrategy, ParallelMode

        caps = [_cap("w0", 0, 24000, 100.0)]
        strategy = ParallelStrategy(mode=ParallelMode.HETEROGENEOUS, dp_degree=1, device_capabilities=caps)
        placer = HeterogeneousPlacer(strategy)
        plan = placer.place_model(simple_model, input_shape=(1, 16))

        assert len(plan.memory_usage_per_device) >= 1
        total_mem = sum(plan.memory_usage_per_device.values())
        # 3 层小 MLP 参数量 < 0.1 MB，激活亦小
        assert total_mem >= 0
        assert total_mem < 100  # MB

    def test_dp_placement_all_devices_same_compute(self, simple_model):
        """数据并行：各设备计算负载相同（全模型复制）"""
        from app.training.distributed.placer import HeterogeneousPlacer
        from app.training.distributed.strategy import ParallelStrategy, ParallelMode

        caps = [_cap(f"w{i}", i, 8000, 50.0) for i in range(2)]
        strategy = ParallelStrategy(mode=ParallelMode.DATA_PARALLEL, dp_degree=2, device_capabilities=caps)
        placer = HeterogeneousPlacer(strategy)
        plan = placer.place_model(simple_model, input_shape=(1, 16))

        balances = list(plan.compute_balance.values())
        assert len(balances) == 2
        assert balances[0] == balances[1]


class TestApplyPlacement:
    """放置计划应用到模型"""

    def test_apply_placement_moves_modules(self):
        from app.training.distributed.placer import HeterogeneousPlacer, apply_placement
        from app.training.distributed.strategy import ParallelStrategy, ParallelMode

        model = torch.nn.Sequential(torch.nn.Linear(8, 8), torch.nn.ReLU(), torch.nn.Linear(8, 2))
        caps = [_cap("w0", 0, 8000, 50.0)]
        strategy = ParallelStrategy(mode=ParallelMode.HETEROGENEOUS, dp_degree=1, device_capabilities=caps)
        plan = HeterogeneousPlacer(strategy).place_model(model, input_shape=(1, 8))

        applied = apply_placement(model, plan)
        # 应用后模型可用（前向传播正常）
        out = applied(torch.randn(1, 8))
        assert out.shape == (1, 2)


class TestVersioning:
    """版本管理：语义化版本解析、递增、比较"""

    def test_parse_semver(self):
        from app.registry.versioning import SemanticVersion

        v = SemanticVersion.parse("1.2.3")
        assert (v.major, v.minor, v.patch) == (1, 2, 3)

    def test_parse_semver_with_prerelease(self):
        from app.registry.versioning import SemanticVersion

        v = SemanticVersion.parse("1.2.3-rc.1")
        assert v.prerelease == "rc.1"

    def test_parse_invalid_version_raises(self):
        from app.registry.versioning import SemanticVersion

        with pytest.raises(ValueError, match="无效的版本格式"):
            SemanticVersion.parse("not-a-version")

    @pytest.mark.parametrize(
        "base,bump,expected",
        [
            ("1.2.3", "patch", "1.2.4"),
            ("1.2.3", "minor", "1.3.0"),
            ("1.2.3", "major", "2.0.0"),
        ],
    )
    def test_increment(self, base, bump, expected):
        from app.registry.versioning import SemanticVersion

        v = SemanticVersion.parse(base)
        result = getattr(v, f"increment_{bump}")()
        assert str(result) == expected

    def test_version_ordering(self):
        from app.registry.versioning import SemanticVersion

        assert SemanticVersion.parse("1.0.0") < SemanticVersion.parse("1.0.1")
        assert SemanticVersion.parse("1.9.9") < SemanticVersion.parse("2.0.0")
        assert SemanticVersion.parse("1.0.0") == SemanticVersion.parse("1.0.0")

    def test_generate_next_version_semver(self):
        from app.registry.versioning import VersionManager

        vm = VersionManager("semver")
        assert vm.generate_next_version([], "patch") == "1.0.0"
        assert vm.generate_next_version(["1.0.0", "1.2.0"], "patch") == "1.2.1"
        assert vm.generate_next_version(["1.2.3"], "minor") == "1.3.0"

    def test_generate_next_version_timestamp(self):
        from app.registry.versioning import VersionManager

        vm = VersionManager("timestamp")
        v = vm.generate_next_version([])
        # 格式 YYYYMMDD-HHMMSS
        assert len(v) == 15 and v[8] == "-"

    def test_compare_and_sort(self):
        from app.registry.versioning import VersionManager

        vm = VersionManager("semver")
        assert vm.compare_versions("1.0.0", "2.0.0") == -1
        assert vm.compare_versions("2.0.0", "1.0.0") == 1
        assert vm.compare_versions("1.0.0", "1.0.0") == 0

        sorted_v = vm.sort_versions(["1.0.0", "2.1.0", "1.5.0"])
        assert sorted_v == ["2.1.0", "1.5.0", "1.0.0"]

    def test_get_latest(self):
        from app.registry.versioning import VersionManager

        vm = VersionManager("semver")
        assert vm.get_latest(["1.0.0", "3.2.1", "2.0.0"]) == "3.2.1"
        assert vm.get_latest([]) is None


class TestPipelineDAG:
    """Pipeline DAG：拓扑排序分层"""

    def _make_pipeline(self, steps_data):
        from app.orchestration.models import Pipeline, PipelineStep

        steps = [PipelineStep(**s) for s in steps_data]
        return Pipeline(id="pipe-test", name="test", steps=steps)

    def test_linear_chain_ordering(self):
        """线性链：A -> B -> C 按序分层"""
        pipeline = self._make_pipeline([
            {"id": "a", "name": "A", "type": "dataset_pull", "config": {}},
            {"id": "b", "name": "B", "type": "training", "config": {}, "depends_on": ["a"]},
            {"id": "c", "name": "C", "type": "model_upload", "config": {}, "depends_on": ["b"]},
        ])
        levels = pipeline.get_execution_order()
        assert levels == [["a"], ["b"], ["c"]]

    def test_parallel_branches_same_level(self):
        """并行分支：B、C 都只依赖 A 时同层"""
        pipeline = self._make_pipeline([
            {"id": "a", "name": "A", "type": "dataset_pull", "config": {}},
            {"id": "b", "name": "B", "type": "training", "config": {}, "depends_on": ["a"]},
            {"id": "c", "name": "C", "type": "custom_script", "config": {}, "depends_on": ["a"]},
        ])
        levels = pipeline.get_execution_order()
        assert levels[0] == ["a"]
        assert sorted(levels[1]) == ["b", "c"]

    def test_diamond_dependency(self):
        """菱形依赖：A -> (B,C) -> D，D 在最后一层"""
        pipeline = self._make_pipeline([
            {"id": "a", "name": "A", "type": "dataset_pull", "config": {}},
            {"id": "b", "name": "B", "type": "dataset_process", "config": {}, "depends_on": ["a"]},
            {"id": "c", "name": "C", "type": "dataset_process", "config": {}, "depends_on": ["a"]},
            {"id": "d", "name": "D", "type": "training", "config": {}, "depends_on": ["b", "c"]},
        ])
        levels = pipeline.get_execution_order()
        assert levels[0] == ["a"]
        assert sorted(levels[1]) == ["b", "c"]
        assert levels[2] == ["d"]

    def test_get_step_found_and_missing(self):
        pipeline = self._make_pipeline([
            {"id": "a", "name": "A", "type": "dataset_pull", "config": {}},
        ])
        assert pipeline.get_step("a").name == "A"
        assert pipeline.get_step("missing") is None


class TestTemplateParams:
    """模板参数验证"""

    def _template(self, params_spec):
        from app.registry.template_registry import ScriptTemplate, TemplateParameter

        return ScriptTemplate(
            id="t-test",
            name="测试模板",
            description="",
            category="classification",
            task_type="classification",
            content="print('{{ value }}')",
            parameters=[TemplateParameter(**p) for p in params_spec],
        )

    def test_missing_required_param_reported(self):
        template = self._template([
            {"name": "dataset_id", "type": "string", "label": "数据集", "required": True},
        ])
        errors = template.validate_params({})
        assert any("dataset_id" in e for e in errors)

    def test_integer_coercion(self):
        """字符串数字自动转整数"""
        template = self._template([
            {"name": "epochs", "type": "integer", "label": "轮数", "required": True},
        ])
        params = {"epochs": "50"}
        errors = template.validate_params(params)
        assert errors == []
        assert params["epochs"] == 50

    def test_invalid_integer_reported(self):
        template = self._template([
            {"name": "epochs", "type": "integer", "label": "轮数", "required": True},
        ])
        errors = template.validate_params({"epochs": "abc"})
        assert len(errors) == 1

    def test_select_option_validation(self):
        template = self._template([
            {"name": "model_type", "type": "select", "label": "模型",
             "options": ["mlp", "xgboost"], "required": True},
        ])
        assert template.validate_params({"model_type": "mlp"}) == []
        errors = template.validate_params({"model_type": "transformer"})
        assert len(errors) == 1

    def test_render_with_params(self):
        template = self._template([
            {"name": "value", "type": "string", "label": "值", "required": True},
        ])
        rendered = template.render({"value": "hello"})
        assert "hello" in rendered
