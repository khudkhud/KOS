from robotos.control.memory.store import MemoryStore
from robotos.control.world_memory import OSMWorldProjector
from robotos.embodied import NavigationGoal, PathNavigationAgent, RobotStateEstimator, SafetySupervisor


def test_pna_and_safety_supervisor_components():
    memory = MemoryStore()
    pna = PathNavigationAgent(memory)
    nav = pna.plan_and_execute(NavigationGoal(semantic_target="entrance"))
    assert nav.success is True

    est = RobotStateEstimator()
    est.update(battery_percent=10)
    safety = SafetySupervisor(min_battery_percent=15)
    decision = safety.evaluate(est.snapshot())
    assert decision.allow_execute is False

def test_dynamic_semantic_topology_update_with_confidence_and_timestamp():
    memory = MemoryStore()
    # same semantic target appears in two regional maps; newer/higher-confidence wins
    memory.upsert_semantic_node(map_id="region_a", node="laundry", waypoints=["hallway", "laundry_a"], confidence=0.62, updated_at=100)
    memory.upsert_semantic_node(map_id="region_b", node="laundry", waypoints=["hallway", "laundry_b"], confidence=0.88, updated_at=200)
    memory.set_active_topologies(["region_a", "region_b"])

    pna = PathNavigationAgent(memory)
    nav = pna.plan_and_execute(NavigationGoal(semantic_target="laundry", constraints={"min_confidence": 0.8}))
    assert nav.success is True
    assert nav.waypoints[-1] == "laundry_b"

def test_semantic_topology_merge_from_multiple_regions():
    memory = MemoryStore()
    memory.upsert_semantic_node(map_id="left_zone", node="living_room", waypoints=["l_corridor", "living_room"], confidence=0.9, updated_at=101)
    memory.upsert_semantic_node(map_id="right_zone", node="balcony", waypoints=["r_corridor", "balcony"], confidence=0.85, updated_at=102)

    merged = memory.merge_topologies(
        new_map_id="home_union",
        source_map_ids=["left_zone", "right_zone"],
        bridge_nodes=[{"node": "center_hall", "waypoints": ["center_hall"], "confidence": 0.8, "updated_at": 103}],
    )
    assert merged["map_id"] == "home_union"

    pna = PathNavigationAgent(memory)
    a = pna.plan_and_execute(NavigationGoal(semantic_target="living_room"))
    b = pna.plan_and_execute(NavigationGoal(semantic_target="balcony"))
    c = pna.plan_and_execute(NavigationGoal(semantic_target="center_hall"))
    assert a.success is True and b.success is True and c.success is True

def test_osm_world_projection_pipeline():
    memory = MemoryStore()
    projector = OSMWorldProjector(memory)
    report = projector.project([
        {"type": "SESSION_STATE_CHANGED", "session_id": "S-1", "payload": {"state": "EXECUTING"}},
        {"type": "ACTION_RESULT", "session_id": "S-1", "payload": {"status": "SUCCEEDED", "error_code": ""}},
    ])
    assert report["session_count"] == 1
    assert memory.read_world_fact("session_summary")["S-1"]["state"] == "EXECUTING"

