import json


def test_why_bt(ncg):
    code, out, _ = ncg("why", "Bot_BT", "5")
    assert code == 0
    assert "#0 Selector" in out and "→ #1 must fail first" in out
    assert "→ #3 must succeed first" in out
    assert "→ at most once per $Cool time\n" in out
    _, out, _ = ncg("why", "Bot_BT", "8")
    assert "→ picked with weight 3.0 (75%)" in out
    _, out, _ = ncg("why", "Bot_BT", "9")
    assert "(stops at a disabled connection)" in out and "connection disabled: never runs" in out
    _, out, _ = ncg("why", "Broken_BT", "6")
    assert "is not reachable from the root" in out


def test_why_merge_and_iterate(ncg, project):
    ops = [
        {"op": "var-add", "name": "Targets", "type": "List<Unit>"},
        {"op": "add", "parent": "6", "id": "m", "node": {"$type": "Merge"}},
        {"op": "connect", "from": "4", "to": "m"},
        {"op": "remove", "node": "5"},
        {
            "op": "add",
            "parent": "m",
            "id": "it",
            "node": {"$type": "Iterator", "targetList": "$Targets", "current": "$Target"},
        },
        {"op": "add", "parent": "it", "node": {"$type": "ActionNode", "_action": {"$type": "Wait"}}},
    ]
    (project / "ops.json").write_text(json.dumps(ops))
    code, out, err = ncg("apply", "Bot_BT", project / "ops.json", "--force")
    assert code == 0, err
    show = ncg("show", "Bot_BT")[1]
    wait_id = next(line.split("#")[1].split()[0] for line in show.splitlines() if "wait 1s" in line)
    _, out, _ = ncg("why", "Bot_BT", wait_id)
    assert "path 1/2:" in out and "path 2/2:" in out
    assert "→ for each element of $Targets, current=$Target" in out
    assert "→ joins several branches" in out


def test_why_expand_through_subtree(ncg):
    _, out, _ = ncg("why", "Sub_BT", "2", "--expand")
    assert "via Bot_BT #8:" in out and "↳ inside Sub_BT:" in out
    assert "via Bot_FSM #0:" in out


def test_why_siblings(ncg, project):
    _, out, _ = ncg("why", "Bot_BT", "5")
    assert "checks:" not in out
    _, out, _ = ncg("why", "Bot_BT", "5", "--siblings")
    assert "→ #1 must fail first\n      #1 checks: @Self.IsDead\n" in out
    assert "→ #3 must succeed first\n      #3 checks: !$Alert\n" in out
    _, out, _ = ncg("why", "Bot_BT", "9", "--siblings")
    assert "#2 checks: all of: !$Alert; result of #4 Cooldown" in out and "then 1 more step (#6)" in out
    # a SubTree sibling is opened into its own graph, resolving the reference through the graph that owns it
    ops = [
        {"op": "add", "parent": "0", "id": "s", "index": 1, "node": {"$type": "SubTree"}},
        {"op": "subgraph-set", "node": "s", "graph": "Sub_BT"},
    ]
    (project / "ops.json").write_text(json.dumps(ops))
    assert ncg("apply", "Bot_BT", project / "ops.json", "--force")[0] == 0
    _, out, _ = ncg("why", "Bot_BT", "5", "--siblings")
    assert "#2 checks: Sub_BT: all of: result of #1 $Alert = true; then 2 more steps (#2, #3)" in out
    _, out, _ = ncg("why", "Bot_BT", "5", "--siblings", "--siblings-depth", "0")
    assert "#2 checks: result of SubTree Sub_BT (raise --siblings-depth to open)" in out


def test_why_siblings_fsm(ncg, project):
    (project / "ops.json").write_text(json.dumps([{"op": "connect", "from": "1", "to": "0"}]))
    assert ncg("apply", "Bot_FSM", project / "ops.json", "--force")[0] == 0
    _, out, _ = ncg("why", "Bot_FSM", "0", "--siblings")
    assert "(after #0 transitions did not fire (checked in order))\n      --[!$Alert]--> #0 did not fire" in out
    _, out, _ = ncg("paths", "--to", "Bot.Attack", "--siblings")
    assert "#1 checks: @Self.IsDead" in out


def test_why_fsm_and_dt(ncg):
    _, out, _ = ncg("why", "Bot_FSM", "0")
    assert "start state" in out and "from #1 Combat --[!$Alert]-->" in out
    assert "from any state via #2 Any --[(always)]-->" in out
    _, out, _ = ncg("why", "Talk_DT", "2")
    assert '#1 choice 1 "Who are you?"' in out and "jump from #5" in out
    _, out, _ = ncg("why", "Talk_DT", "3")
    assert 'choice 2 "Let me pass." if $Met' in out


def test_paths(ncg):
    _, out, _ = ncg("paths", "--to", "Bot.Attack")
    assert "== Assets/Graphs/Bot_BT.asset" in out and "-- #5 @Self.Attack($Target)" in out
    _, out, _ = ncg("paths", "--to", "$Alert")
    assert "-- #1 $Alert = true" in out and "#3 ? !$Alert" not in out  # writes only
    _, out, _ = ncg("paths", "--to", "$Alert", "--reads")
    assert "-- #3 ? !$Alert" in out and "-- transition #0->#1 --[$Alert]-->" in out
    _, out, _ = ncg("paths", "Bot_BT", "--to", "$Nope")
    assert "no writes of $Nope in Bot_BT" in out


def test_stats(ncg):
    _, out, _ = ncg("stats", "Bot_BT")
    assert "nodes=10 connections=9 variables=5 depth=4" in out
    assert "roles: composite 3, action 3, condition 2, decorator 1, subgraph 1" in out
    assert "uses: Sub_BT" in out
    _, out, _ = ncg("stats")
    assert "shared subgraphs:\n  Sub_BT ← Bot_BT, Bot_FSM" in out
    data = json.loads(ncg("stats", "--json")[1])
    assert data["totals"]["kinds"]["bt"] == 5
    sub = next(g for g in data["graphs"] if g["name"] == "Sub_BT")
    assert sub["used_by"] == ["Bot_BT", "Bot_FSM"]
