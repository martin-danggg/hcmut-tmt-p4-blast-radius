# pytest plugin: write failing test ids (failures, errors, failed subtests, collection errors) to BR_FAIL_OUT
import os
_F = set()
def pytest_runtest_logreport(report):
    if report.failed:
        _F.add(report.nodeid.split(" ")[0])
def pytest_collectreport(report):
    if report.failed:
        _F.add("COLLECT::" + report.nodeid)
def pytest_sessionfinish(session, exitstatus):
    with open(os.environ["BR_FAIL_OUT"], "w") as fh:
        fh.write("\n".join(sorted(_F)) + "\nEND\n")
