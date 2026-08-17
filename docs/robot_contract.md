# Robot contract

What a robot must implement to be discovered by the mission planner and receive work.

Three HTTP calls out, one TCP listener. Nothing else. A complete, runnable reference
implementation lives in [`scripts/mock_robot.py`](../scripts/mock_robot.py) — standard library
only, so it also runs on the robot as-is. Develop against it before wiring up the real thing.

The planner and the robots are assumed to be on the same network, but **not** the same machine.

---

## 0. What the robot needs to know

One value: the planner's discovery URL, `http://<planner-host>:8003`. The port is fixed by
`fleet.discovery.port` and is the same whether the operator launched the web app or the CLI.

If `fleet.auth_token` is set on the planner, every call below must also carry that value in an
`X-Fleet-Token` header. It is unset by default.

---

## 1. Register

Once, at robot startup.

```http
POST /robots/register
Content-Type: application/json
```

```json
{
  "robot_id": "amiga-01",
  "name": "Amiga #1",
  "schema": "amiga_btcpp",
  "schema_sha256": "9f2c...",
  "bt_endpoint": {
    "host": null,
    "port": 12346,
    "protocol": "tcp-lenprefix-v1"
  },
  "capabilities": {
    "actions": ["MoveToTreeID", "MoveToAisleHead", "SampleLeaf"],
    "has_manipulator": true
  },
  "status": "idle",
  "battery_pct": 0.87,
  "position": {"lat": 37.361, "lon": -120.4318},
  "heartbeat_interval_s": 5
}
```

| Field | Required | Notes |
| --- | --- | --- |
| `robot_id` | yes | Stable across reboots (serial or hostname). Used in URLs and log filenames, so no `/` or `\`. |
| `schema` | yes | XSD stem naming the action pool this robot executes, e.g. `amiga_btcpp`. `amiga_btcpp.xsd` and `schemas/amiga_btcpp.xsd` are also accepted and reduced to the stem. |
| `bt_endpoint.port` | yes | Where your behavior-tree receiver listens. |
| `bt_endpoint.host` | no | **Send `null`.** See below. |
| `schema_sha256` | no | sha256 of the robot's copy of the XSD. Lets the planner warn on drift. |
| `capabilities.actions` | no | Subset of the schema's actions this robot can actually execute. Omit to mean "all of them". Extra keys are allowed and preserved. |
| `status` | no | `idle` \| `busy` \| `error`. Defaults to `idle`. |
| `battery_pct` | no | 0.0–1.0. Robots below `fleet.allocation.min_battery_pct` are skipped. |
| `heartbeat_interval_s` | no | Defaults to 5. |

Response:

```json
{
  "ok": true,
  "robot_id": "amiga-01",
  "ttl_s": 15,
  "heartbeat_url": "/robots/amiga-01/heartbeat",
  "schema_known": true,
  "schema_matches": true
}
```

**Send `bt_endpoint.host: null`.** A robot rarely knows which of its interfaces the planner can
reach, and an address that is correct locally (`127.0.0.1`, `0.0.0.0`) is useless to the planner.
With `null`, the planner uses the source IP of the registration request, which is by construction
an address that reaches you — including through NAT, such as the planner running in a
port-mapped container. Send an explicit host only when you are behind a port-forward whose external
address the planner cannot infer.

**Registration is idempotent.** Re-POST on reconnect, on config change, or whenever a heartbeat
returns 404. The same `robot_id` replaces the previous record.

**`schema_known: false`** means the planner has no `schemas/<schema>.xsd`. The robot still appears
in the roster — so an operator can see it and diagnose the mismatch — but it will never be given
work. **`schema_matches: false`** means the planner has that schema but its bytes differ from the
`schema_sha256` you sent; plans may reference actions your build does not implement.

---

## 2. Heartbeat

Every `heartbeat_interval_s`.

```http
POST /robots/amiga-01/heartbeat
```

```json
{
  "status": "busy",
  "battery_pct": 0.83,
  "position": {"lat": 37.3612, "lon": -120.4315},
  "current_mission_id": "a3f1c9"
}
```

All fields optional; an empty body `{}` is a valid liveness ping. Response is `{"ok": true}`.

**A 404 means the planner restarted and lost the roster.** Re-register and carry on — this is the
expected recovery path, not an error.

How the planner reads silence:

| Since last heartbeat | State | Given new work? |
| --- | --- | --- |
| ≤ `stale_after_s` (8s) | `online` | yes, if `idle` |
| ≤ `heartbeat_ttl_s` (15s) | `stale` | no |
| beyond that | `offline` | no |

Robots stay visible in the UI while offline, then are forgotten after a longer interval. Only
`online` **and** `idle` robots with a known schema and enough battery are eligible for allocation,
so **set `status` to `busy` while executing and back to `idle` when done** — otherwise the planner
will hand you a second mission on top of the first.

---

## 3. Deregister

On clean shutdown. Optional — skipping it just means the TTL takes ~15s to notice.

```http
DELETE /robots/amiga-01
```

---

## 4. Receive a mission

**The robot is the listener.** The planner is the TCP *client* for missions: it connects out to
`bt_endpoint.port` on your machine. It never listens on that port itself, which is why the mission
port is deliberately not published by the planner's container — doing so would make docker-proxy
bind it on the host and steal it from a robot running there.

Frames 1 and 2 are unchanged from the single-robot protocol; frame 3 is the only addition, and it
comes from you.

```
PLANNER -> ROBOT   [uint32 BE len][ XML behavior tree                   ]
PLANNER -> ROBOT   [uint32 BE len][ JSON tree points + mission metadata ]   (omitted if no orchard)
                   -- planner half-closes its write side here --
ROBOT   -> PLANNER [uint32 BE len][ JSON ack                            ]
```

Lengths are 4-byte big-endian unsigned ints. XML is raw bytes; JSON is UTF-8.

### Knowing when the payload ends

After its last frame the planner calls `shutdown(SHUT_WR)`. Your reads hit EOF exactly as they did
before — so a "read frames until `recv` returns empty" loop is still correct — but the socket
remains writable in your direction, so you can still send the ack. This is the one detail worth
getting right: if the planner simply held the connection open waiting for an ack, a receiver
looping until EOF would deadlock.

### The ack

```json
{"accepted": true, "robot_id": "amiga-01", "mission_id": "a3f1c9", "error": null}
```

Reject with `{"accepted": false, ..., "error": "battery too low"}` and the planner reports that
robot as `rejected` instead of claiming success. If you close without acking, the planner records
`unacknowledged` and moves on — so an existing receiver that has not implemented this yet keeps
working.

### The mission metadata

Frame 2 is the orchard map you already receive, plus one additive `mission` key. Every existing
key is unchanged.

```json
{
  "traversal_axis": "column",
  "trees": [{"tree_index": 1, "row": 1, "col": 1, "lat": 37.36, "lon": -120.43, "row_waypoints": []}],
  "aisle_entrances": [{"entrance_index": 1, "lat": 37.36, "lon": -120.43}],
  "aisle_to_entrance_indices": {"1": [1, 2]},

  "mission": {
    "mission_id": "a3f1c9",
    "robot_id": "amiga-01",
    "assigned_tree_indices": [1, 2, 3],
    "assigned_aisles": [1, 2, 3, 4],
    "fleet": [
      {"robot_id": "amiga-02", "assigned_aisles": [5, 6, 7], "assigned_tree_indices": [73, 74]}
    ]
  }
}
```

- The **full** orchard map goes to every robot; `assigned_*` scopes what this robot is responsible
  for.
- Aisles are the driving lanes **between** tree lines, numbered from 1. With `traversal_axis:
  "row"` there are `rows - 1` of them and aisle *k* lies between row *k* and row *k+1*; with
  `"column"` the same holds for columns.
- `fleet` is advisory — it names the zones other robots own so you can stay out of them.
- Echo `mission_id` back in your ack, and report it as `current_mission_id` in heartbeats while you
  execute. That is what closes the loop for the operator.

---

## 5. How the planner uses all this

1. Every eligible robot's schema is collected and **deduplicated** — two Amigas contribute one copy
   of `amiga_btcpp.xsd` to the prompt.
2. One LLM call divides the mission across the roster, honouring each robot's action pool.
3. One behavior tree is generated per robot, validated against **every** available schema until one
   accepts it.
4. Each tree is sent to the `bt_endpoint` that robot registered.

Routing keys on `robot_id`, not on which schema validated: two robots running the same XSD produce
two plans that validate identically, so the schema alone cannot tell them apart. The matched schema
is used as a cross-check — a plan generated for an Amiga that only validates against
`clearpath_husky.xsd` is treated as a mis-generation and retried.

---

## 6. Trying it

```bash
# terminal 1 - planner (needs fleet.enabled: true in the config)
make webapp

# terminals 2 and 3 - two robots on different BT ports
python3 scripts/mock_robot.py --robot-id amiga-01 --bt-port 12346 --planner http://localhost:8003
python3 scripts/mock_robot.py --robot-id amiga-02 --bt-port 12347 --planner http://localhost:8003

# confirm discovery
curl -s http://localhost:8003/robots | python3 -m json.tool
```

Then ask for a mission covering the whole orchard and watch it split across the two.

Useful flags on the mock: `--reject "battery too low"` to exercise the rejection path, `--no-ack`
to imitate a receiver that predates the ack frame, `--schema clearpath_husky` to test a mixed
fleet, and `--print-xml` to dump the behavior tree it received.

If your real BT executor is already listening on the port, pass `--no-listen` and the mock becomes
a discovery client only — registration and heartbeats — while missions go to your executor. That
is the same role [`scripts/bot.py`](../scripts/bot.py) fills:

```bash
python3 scripts/mock_robot.py --robot-id amiga-01 --bt-port 12346 --no-listen
```

`Address already in use` on `--bt-port` means something already holds it: either your real
executor (use `--no-listen`) or another mock (use a different `--bt-port`).
