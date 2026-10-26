# Planner configuration

Each YAML file in this folder is a full planner configuration. Pick one with:

```bash
CONFIG=./app/config/<name>.yaml make run   # CLI (default: localhost.yaml)
```

The web app (`make webapp` / `make prod`) always loads `localhost.yaml`.

Relative paths are resolved against the working directory first, then against
this folder.

| File | Purpose |
| --- | --- |
| `localhost.yaml` | Default config for local development and the web app. |
| `kinova.yaml` | Kinova manipulator only. |
| `husky_fv.yaml` | Husky with formal verification. |
| `kinova_husky.yaml` | Kinova mounted on a Husky. |
| `farm_polygons/` | Orchard layouts referenced by `mission.farm.polygon_file`. |

## `logging`

| Key | Type | Description |
| --- | --- | --- |
| `level` | string | Python log level: `DEBUG`, `INFO`, `WARNING`, `ERROR`. `DEBUG` also logs every LLM request payload. |

## `auth`

| Key | Type | Description |
| --- | --- | --- |
| `token_env_file` | path | `.env` file holding the provider API keys (e.g. `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`). |

## `planner`

### `planner.retries`

| Key | Type | Description |
| --- | --- | --- |
| `max` | int | Maximum number of times the LLM may try to fix a plan that fails XML or LTL validation. |

### `planner.generation`

| Key | Type | Description |
| --- | --- | --- |
| `max_tokens` | int | Maximum tokens in each LLM response. |
| `reasoning_effort` | string | Reasoning effort sent to every model (`none`, `low`, `medium`, `high`). Defaults to `high`. Temperature is never sent. |

### `planner.validation`

| Key | Type | Description |
| --- | --- | --- |
| `lint_xml` | bool | Validate the generated mission XML against the schemas in `mission.schema_paths`. The web app can override this per request. |
| `ltl_enabled` | bool | Turn on formal verification: generate an LTL spec for each mission and check it with SPOT and Spin. Requires `promela_template` and `spin_path`, and an image built with `ENABLE_VERIFICATION=true`. |

### `planner.formal_verification`

Only used when `planner.validation.ltl_enabled` is `true`.

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `promela_template` | path | | Promela template that the mission XML is compiled into. |
| `spin_path` | path | | Spin executable, e.g. `spin` if it is on `PATH`. |
| `human_review` | bool | `false` | `true`: you approve the example runs at a `y/n` prompt. `false`: the LLM arbiter (`llm.routing.arbiter`) approves them. CLI only; the web app always uses the arbiter. |
| `example_runs` | int | `5` | Number of accepting runs sampled from the LTL automaton and shown to the reviewer. |

## `llm`

Model names use [LiteLLM](https://docs.litellm.ai/docs/providers) format, e.g.
`openai/gpt-5.2`, `claude-sonnet-4-20250514`, `hosted_vllm/openai/gpt-oss-120b`.

### `llm.routing`

| Key | Type | Description |
| --- | --- | --- |
| `mode` | string | `online`: always use `online_model`. `offline`: always use `offline_model`. `auto`: use `online_model` when the internet is reachable, otherwise `offline_model`. |
| `online_model` | string | Model for mission XML generation when online. Required. |
| `offline_model` | string | Model for mission XML generation when offline. Falls back to `online_model` if unset. |
| `ltl.online_model` | string \| null | LTL generation model when online. `null` = same as `online_model`. |
| `ltl.offline_model` | string \| null | LTL generation model when offline. `null` = same as `offline_model`. |
| `arbiter.online_model` | string \| null | Model that reviews the example runs in place of a human, when online. `null` = same as `online_model`. |
| `arbiter.offline_model` | string \| null | Arbiter model when offline. `null` = same as `offline_model`. |

The `ltl` and `arbiter` blocks are optional. Without them, every step uses the
mission XML model.

### `llm.endpoints`

| Key | Type | Description |
| --- | --- | --- |
| `online_api_base` | URL \| null | API base for online models. `null` = the provider's default endpoint. |
| `offline_api_base` | URL \| null | API base for offline models, e.g. a local vLLM server (`http://172.17.0.1:8000/v1`). |

Each mode's endpoint is shared by all three models (XML, LTL, arbiter) in that mode.

## `mission`

| Key | Type | Description |
| --- | --- | --- |
| `log_directory` | path | Where generated XML, LTL, Promela and payloads are written. Created if missing. |
| `schema_paths` | list[path] | XSD schemas the generated XML must match. In fleet mode, the web app uses the schemas of the selected robots instead. |
| `context_files` | list[path] | Prompt and context files (text, GeoJSON, etc.) added to the system prompt. `.j2` files are rendered as Jinja templates. The web app preselects these and lets you change them. |
| `farm.polygon_file` | path | Orchard layout from `farm_polygons/`. Omit it for non-orchard environments. |
| `farm.polygon` | mapping | Inline orchard layout, used if `polygon_file` is missing or unreadable. Same format as a polygon file. |

### Farm polygon files

```yaml
points:               # corners, clockwise from the north-west corner
  - {lon: -120.43214356, lat: 37.36138382}
  - ...
dimensions:           # tree grid, counted from north to south
  - {row: 8, col: 18}
traversal_axis: "row" # "row" or "column" (default "row")
perimeter_margin_m: 5.0  # optional, default 5.0
```

List `points` in the order north-west, north-east, south-east, south-west.
In any other order the web app can't draw the trees in the right places.

## `network`

| Key | Type | Description |
| --- | --- | --- |
| `tcp.host` | string | Robot to send missions to when the fleet is disabled or no robot has registered. The web app can override it per request. |
| `tcp.port` | int | Port for `tcp.host`. |

## `fleet`

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `enabled` | bool | `false` | Turn on robot discovery and multi-robot allocation. |
| `discovery.host` | string | `0.0.0.0` | Interface for the discovery API. Robots `POST /robots/register` and then send heartbeats. |
| `discovery.port` | int | `8003` | Port for the discovery API. |
| `discovery.heartbeat_ttl_s` | float | `15` | Seconds without a heartbeat before a robot is treated as offline. |
| `discovery.stale_after_s` | float | `8` | Seconds without a heartbeat before a robot is marked stale and gets no new work. |
| `auth_token` | string \| null | `null` | If set, every discovery call must send it in an `X-Fleet-Token` header. |
| `registry_file` | path \| null | `logs/robots.json` | Where registered robots are saved across restarts. `null` keeps them in memory only. |
| `static_robots` | list | `[]` | Robots that can't register themselves; they are always treated as online. Each entry has `robot_id`, `schema`, `port` and optionally `host`, `name`, `actions`. |
| `allocation.require_idle` | bool | `true` | Only assign missions to robots whose status is `idle`. |
| `allocation.min_battery_pct` | float | `0.0` | Minimum battery level as a fraction (`0.2` = 20%). `0` turns the check off. |
| `allocation.max_robots_per_mission` | int \| null | `null` | Upper limit on the number of robots per mission. `null` = no limit. |

Example static robot:

```yaml
static_robots:
  - robot_id: husky-bench
    schema: clearpath_husky   # schemas/<schema>.xsd
    host: 192.168.1.20
    port: 12346
```
