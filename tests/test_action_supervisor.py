from robotos.app import build_system
from robotos.kernel.action.dds import DDSActionClient, InMemoryDDSBroker
from robotos.kernel.action.supervisor import ActionSupervisor
from robotos.kernel.lease.manager import LeaseManager
from robotos.kernel.osm.store import OSMStore
from robotos.kernel.policy.gate import ToolRegistry
from robotos.kernel.scheduler.mixed import MixedWorkloadScheduler
from robotos.models import Session, SessionState
from robotos.skills.ai_model_skills import DepthAnythingSkill, NavDPSkill, RoboBrainSkill, YOLOPerceptionServiceSkill


def test_tool_contract_compatibility_with_major_wildcard():
    reg = ToolRegistry.from_json_file("tool_registry.json")
    spec = reg.negotiate("nav.goto", accepted_contracts=["1.x"])
    assert spec.contract_version.startswith("1.")

def test_task_agent_recovery_unknown_target_fallback_to_entrance():
    sys = build_system()
    api = sys["api"]
    task_agent = sys["task_agent"]

    session_id = api.post_sessions({"owner": "voice", "capabilities": ["NAV", "DIALOG"], "preemption_policy": "PAUSEABLE"})["session_id"]
    task_agent.submit_long_nav_task(session_id, "nonexistent_room")

    assert len(task_agent.task_reports) >= 1
    report = task_agent.task_reports[-1]
    assert report["success"] is True
    assert report["target"] == "entrance"
    assert report["recovery_action"] == "fallback_target"

def test_tool_registry_file_load():
    reg = ToolRegistry.from_json_file("tool_registry.json")
    assert reg.get("nav.goto").capability == "NAV"

def test_tool_registry_discovery_and_negotiate():
    reg = ToolRegistry.from_json_file("tool_registry.json")
    dialog_tools = reg.discover(capability="DIALOG")
    assert any(t.tool == "dialog.say" for t in dialog_tools)
    spec = reg.negotiate("nav.goto", accepted_contracts=["1.1", "1.0"])
    assert spec.contract_version == "1.1"

def test_ai_model_skill_stubs():
    yolo = YOLOPerceptionServiceSkill().infer({"frame": "mock"})
    depth = DepthAnythingSkill().infer({"frame": "mock"})
    navdp = NavDPSkill().infer({"rgb": "mock", "goal": "kitchen"})
    brain = RoboBrainSkill().infer({"intent": "去厨房看看"})

    assert yolo["mode"] == "periodic_service"
    assert depth["mode"] == "on_demand_skill"
    assert navdp["result"]["waypoint"].startswith("stub_")
    assert brain["result"]["task_graph"].startswith("stub_")

def test_registry_contains_ai_network_tools():
    reg = ToolRegistry.from_json_file("tool_registry.json")
    assert reg.get("perception.yolo.detect").capability == "PERCEPTION"
    assert reg.get("perception.depth_anything.estimate").capability == "PERCEPTION"
    assert reg.get("navigation.navdp.predict_waypoint").capability == "NAV"
    assert reg.get("planning.robobrain.plan").capability == "PLANNING"

def test_hpu_resources_unified_in_registry():
    reg = ToolRegistry.from_json_file("tool_registry.json")
    assert reg.get("perception.depth_anything.estimate").required_resources == ["hpu"]
    assert reg.get("navigation.navdp.predict_waypoint").required_resources == ["hpu"]
    assert reg.get("planning.robobrain.plan").required_resources == ["hpu"]

def test_navdp_degrades_to_logic_when_hpu_busy():
    osm = OSMStore()
    s = Session(session_id="S-1", owner="o", priority=1, capabilities=["NAV"], state=SessionState.EXECUTING)
    holder = Session(session_id="S-hold", owner="o", priority=1, capabilities=["NAV"], state=SessionState.EXECUTING)
    osm.apply_patch({"type": "session_upsert", "session": s})
    osm.apply_patch({"type": "session_upsert", "session": holder})

    leases = LeaseManager(osm)
    leases.acquire(["hpu"], "S-hold", ttl_ms=5000)

    scheduler = MixedWorkloadScheduler(max_parallel_model=1)
    reg = ToolRegistry.from_json_file("tool_registry.json")
    sup = ActionSupervisor(osm, DDSActionClient(InMemoryDDSBroker()), scheduler=scheduler, leases=leases, tool_registry=reg)

    h = sup.send_goal("S-1", "navigation.navdp.predict_waypoint", {"goal": "kitchen"})
    assert h.tool == "navigation.navdp.logic_predict_waypoint"
    assert any(e.type == "MODEL_DEGRADE_FALLBACK" for e in osm.event_log)

def test_hpu_queue_and_priority_preempt():
    osm = OSMStore()
    low = Session(session_id="S-low", owner="o", priority=1, capabilities=["NAV"], state=SessionState.EXECUTING)
    high = Session(session_id="S-high", owner="o", priority=10, capabilities=["NAV"], state=SessionState.EXECUTING)
    peer = Session(session_id="S-peer", owner="o", priority=1, capabilities=["NAV"], state=SessionState.EXECUTING)
    osm.apply_patch({"type": "session_upsert", "session": low})
    osm.apply_patch({"type": "session_upsert", "session": high})
    osm.apply_patch({"type": "session_upsert", "session": peer})

    leases = LeaseManager(osm)
    leases.acquire(["hpu"], "S-low", ttl_ms=5000)

    scheduler = MixedWorkloadScheduler(max_parallel_model=1)
    sup = ActionSupervisor(osm, DDSActionClient(InMemoryDDSBroker()), scheduler=scheduler, leases=leases)

    # higher-priority session preempts HPU and dispatches
    h = sup.send_goal("S-high", "planning.robobrain.plan", {"intent": "test"})
    assert h.session_id == "S-high"
    lease_id = leases.by_resource.get("hpu")
    assert lease_id is not None
    assert osm.lease_projection[lease_id].owner_session == "S-high"

    # equal-priority peer request should be queued while model quota is occupied
    try:
        sup.send_goal("S-peer", "planning.robobrain.plan", {"intent": "peer"})
        raise AssertionError("expected queued signal")
    except RuntimeError as exc:
        assert str(exc).startswith("HPU_QUEUED")

