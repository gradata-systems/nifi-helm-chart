import logging
import uuid

import pytest

from support import environment

log = logging.getLogger(__name__)


def pytest_addoption(parser):
    group = parser.getgroup("nifi-e2e")
    group.addoption("--controller", default=",".join(environment.CONTROLLERS),
                    help=f"Comma-separated ingress controllers to test (default: all of {', '.join(environment.CONTROLLERS)})")
    group.addoption("--fresh", action="store_true",
                    help="Uninstall the NiFi releases and delete their data before testing")
    group.addoption("--delete-cluster", action="store_true", help="Delete the kind cluster after the tests")


def pytest_generate_tests(metafunc):
    if "controller" in metafunc.fixturenames:
        controllers = [c.strip() for c in metafunc.config.getoption("controller").split(",") if c.strip()]
        unknown = set(controllers) - set(environment.CONTROLLERS)
        if unknown:
            raise pytest.UsageError(f"Unknown controller(s): {', '.join(sorted(unknown))}")
        # Session scope groups the tests by controller, so each environment is set up once
        metafunc.parametrize("controller", controllers, scope="session")


def pytest_collection_modifyitems(config, items):
    """Run all tests for one controller before moving on, so each environment is set up only once."""
    order = {c: i for i, c in enumerate(environment.CONTROLLERS)}
    # Offload restarts a node, so run it last for each controller
    items.sort(key=lambda item: (order.get(getattr(item, "callspec", None) and item.callspec.params.get("controller"), -1),
                                 item.get_closest_marker("slow") is not None))


@pytest.fixture(scope="session")
def infrastructure(request):
    environment.ensure_cluster()
    environment.install_infrastructure()
    if request.config.getoption("fresh"):
        environment.delete_releases()
    yield
    if request.config.getoption("delete_cluster"):
        environment.delete_cluster()


@pytest.fixture(scope="session")
def env(infrastructure, controller, tmp_path_factory):
    log.info("Setting up the %s environment", controller)
    e = environment.start(controller, tmp_path_factory.mktemp(controller))
    yield e
    e.close()


@pytest.fixture
def name():
    """Unique name for the test's NiFi components. The `e2e-` prefix marks leftovers for cleanup."""
    return f"e2e-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def target_group(env, name):
    group = env.target.create_process_group(name)
    yield group
    try:
        env.target.delete_process_group(group)
    except Exception:  # noqa: BLE001 - leftovers are removed at the next session start
        log.warning("Couldn't delete process group %s on the target", group, exc_info=True)


@pytest.fixture
def client_group(env, name):
    group = env.client.create_process_group(name)
    yield group
    try:
        env.client.delete_process_group(group)
    except Exception:  # noqa: BLE001
        log.warning("Couldn't delete process group %s on the client", group, exc_info=True)
