"""Fleet discovery, allocation, and dispatch.

Robots self-report over HTTP (see ``service.py``) into an in-process
``RobotRegistry``. During planning the registry supplies the roster of eligible
robots; the allocator splits the mission across them; the dispatcher fans the
generated behavior trees out to each robot's own BT endpoint.

The robot-side contract is documented in ``docs/robot_contract.md`` and has a
runnable reference implementation in ``scripts/mock_robot.py``.
"""
