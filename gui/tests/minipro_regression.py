"""Regression checks for the front-end defects that were remediated.

Each block names the defect it guards against, so a change that reintroduces
one of them fails here instead of on real hardware.  Command lines are checked
against src/main.c and src/database.c of the engine; the cancel and close paths
run against two stand-ins that behave like minipro, so a long call can be
exercised without a programmer attached.  The suite runs from an unrelated
directory on purpose: the engine finds its databases through the working
directory or MINIPRO_HOME, and that is itself one of the behaviours under test.
"""
import importlib.util
import os
import signal
import subprocess
import sys
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
GUI_PATH = os.path.join(ROOT, "gui", "minipro_gui.py")
SLOW_STUB = os.path.join(HERE, "fakeminipro_slow.sh")
STUBBORN_STUB = os.path.join(HERE, "fakeminipro_stubborn.sh")
MISSING_BINARY = os.path.join(HERE, "minipro-that-does-not-exist")
DUMP = "/tmp/minipro-regression-dump.bin"

for stand_in in (SLOW_STUB, STUBBORN_STUB):
    if not os.access(stand_in, os.X_OK):
        raise SystemExit("missing stand-in: " + stand_in)

spec = importlib.util.spec_from_file_location("mg", GUI_PATH)
mg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mg)

sys.argv = ["minipro_regression.py"]
app = mg.QtWidgets.QApplication(sys.argv)

# A short call can start and finish before a test gets around to watching it,
# so every completion the runner reports is recorded from here on.  Each record
# holds the exit code and whether the user had cancelled, which is exactly the
# pair the front-end turns into the message the operator reads.
REPORTED_FINISH = mg.Runner._finish
FINISHED = {}


def _record_finish(self, exit_code):
    FINISHED.setdefault(id(self), []).append((int(exit_code), self.cancelled))
    REPORTED_FINISH(self, exit_code)


mg.Runner._finish = _record_finish


def records(runner):
    """Return the completions one runner has reported and not yet consumed."""
    return FINISHED.setdefault(id(runner), [])


def run(runner, trigger, timeout_ms=30000):
    """Trigger one action and report how the engine call it started ended.

    Returns the (exit code, cancelled) pair, or None when nothing finished.
    """
    pending = records(runner)
    del pending[:]
    trigger()
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        mg.QtWidgets.QApplication.processEvents()
        if pending:
            return pending.pop(0)
        time.sleep(0.02)
    return None


def settle(runner, timeout_ms=30000):
    """Wait for the completion of a call that was already started."""
    pending = records(runner)
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        mg.QtWidgets.QApplication.processEvents()
        if pending:
            return pending.pop(0)
        time.sleep(0.02)
    return None


def pick(combo, value):
    """Select one combo entry by its stored data, never by index."""
    position = combo.findData(value)
    assert position >= 0, "missing entry %r" % value
    combo.setCurrentIndex(position)


def choose(combo, text):
    """Select one combo entry by its visible text, never by index."""
    position = combo.findText(text)
    assert position >= 0, "missing entry %r" % text
    combo.setCurrentIndex(position)


def reset(window):
    """Return every control of one window to its untouched state."""
    window.advanced.setChecked(False)
    for combo in (window.memory, window.cfg_section, window.format,
                  window.icsp, window.size_mode, window.ovc, window.vcc,
                  window.vpp, window.vdd, window.spi_clock):
        combo.setCurrentIndex(0)
    for box in (window.skip_erase, window.skip_verify, window.skip_blank,
                window.unprotect, window.skip_id, window.id_error,
                window.protect):
        box.setChecked(False)
    window.pulse.setText("")
    window.address.setText("")


def survivors(pattern):
    """Report the processes still matching one pattern, so leaks stay visible."""
    done = subprocess.run(["pgrep", "-f", pattern], capture_output=True)
    return done.stdout.decode("utf-8", "replace").split()


window = mg.MiniproGui(mg.find_binary())
window.show()
print("cwd:", os.getcwd())

# Remediation 7: minipro reads infoic.xml and logicic.xml from its working
# directory or from MINIPRO_HOME, so a front-end started elsewhere listed
# nothing at all and said so only by leaving the list empty.
assert mg.database_home(window.binary) == ROOT, mg.database_home(window.binary)
assert window.database_home == ROOT, window.database_home

assert settle(window.runner), "startup presence call did not finish"
print("programmer:", window.programmer_label.text())
print("startup status:", window.status.text())
journal = window.log.toPlainText()
assert " -k" in journal, journal
assert "Bazele de date" in journal, journal

assert run(window.runner, window.show_engine_info), "version call hung"
print("engine:", window.engine_label.text())
assert window.engine_version and window.engine_commit, "-V gave no identity"

# Remediation 2: the engine info view used to replace the whole journal with
# its own text, dropping the command line that had just been logged.
journal = window.log.toPlainText()
assert " -k" in journal and " -V" in journal, journal
assert journal.count("minipro version") == 1, "the report was shown twice"

# Remediation 3: main.c forces page = CONFIG for --fuses, --uid and --lock, so
# the command line shown must carry the region the engine really programs.
reset(window)
pick(window.cfg_section, "--fuses")
fuses_args = window._op_args("read", "ATMEGA328P@IPD28", "/tmp/f.hex")
assert fuses_args == ["-p", "ATMEGA328P@IPD28", "-c", "config", "--fuses",
                      "-r", "/tmp/f.hex"], fuses_args
reset(window)
pick(window.memory, "code")
pick(window.cfg_section, "--uid")
uid_args = window._op_args("read", "ATMEGA328P@IPD28", "/tmp/f.hex")
assert uid_args == ["-p", "ATMEGA328P@IPD28", "-c", "config", "--uid",
                    "-r", "/tmp/f.hex"], uid_args
reset(window)
pick(window.memory, "data")
pick(window.cfg_section, "--lock")
lock_args = window._op_args("read", "ATMEGA328P@IPD28", "/tmp/f.hex")
assert lock_args == ["-p", "ATMEGA328P@IPD28", "-c", "config", "--lock",
                     "-r", "/tmp/f.hex"], lock_args
assert "forțează" in window.log.toPlainText(), "the override was silent"
print("config filter args:", fuses_args, uid_args, lock_args)
reset(window)
pick(window.memory, "config")
config_args = window._op_args("read", "ATMEGA328P@IPD28", "/tmp/f.hex")
assert config_args.count("-c") == 1, config_args

# Remediation 4: the status line used to state the verb of the action twice.
assert mg.ACTION_TITLES["read"] == "Citire"
for title in mg.ACTION_TITLES.values():
    assert title == title.strip() and "  " not in title, title
assert window.status.text() == "Informatii motor terminata.", window.status.text()
for word in ("Informatii", "motor"):
    assert window.status.text().split().count(word) == 1, window.status.text()
print("action titles:", sorted(mg.ACTION_TITLES.values()))


# Remediation 1: a call the user stopped used to be reported as a plain failure.
slow = mg.MiniproGui(SLOW_STUB)
slow.show()
assert settle(slow.runner), "the stand-in presence call did not finish"
slow.devices.setEditText("W25Q16@SOIC8")
slow.file.setText(DUMP)
slow.do_read()
assert slow.runner.running(), "the stand-in read did not start"
mg.QtWidgets.QApplication.processEvents()
assert slow.status.text() == "Citire", slow.status.text()
slow.do_cancel()
exit_code, cancelled = settle(slow.runner) or (None, False)
assert cancelled, "a call the user stopped was not reported as cancelled"
assert slow.status.text() == "Operatie anulata.", slow.status.text()
assert "Citire anulata" in slow.log.toPlainText(), slow.log.toPlainText()
assert not slow.runner.running(), "the cancelled call is still attached"
slow.do_cancel()
assert slow.status.text() == "Nu exista nicio operatie in desfasurare.", \
    slow.status.text()
slow.close()
mg.QtWidgets.QApplication.processEvents()

# Remediation 5: a wait that never escalated left minipro running after the
# window closed.  This stand-in ignores SIGTERM, as a blocked write can.
leftover = survivors(os.path.basename(STUBBORN_STUB))
if leftover:
    # Only an interrupted earlier run can leave one behind; clear it by pid so
    # this run stays repeatable and still reports what it found.
    print("clearing a stand-in left by an interrupted run:", leftover)
    for pid in leftover:
        try:
            os.kill(int(pid), signal.SIGKILL)
        except OSError:
            pass
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and survivors(os.path.basename(STUBBORN_STUB)):
        time.sleep(0.05)
    assert not survivors(os.path.basename(STUBBORN_STUB)), survivors(STUBBORN_STUB)
stubborn = mg.Runner()
assert stubborn.start(STUBBORN_STUB, ["-w", DUMP], "Scriere")
deadline = time.monotonic() + 10.0
while time.monotonic() < deadline and "ready" not in stubborn.stderr_text:
    mg.QtWidgets.QApplication.processEvents()
    time.sleep(0.02)
assert "ready" in stubborn.stderr_text, stubborn.stderr_text
assert stubborn.running()
assert stubborn.wait(1000) is True, "wait did not report the process as stopped"
assert not stubborn.running(), "the stubborn process is still attached"
assert not survivors(STUBBORN_STUB), survivors(STUBBORN_STUB)
print("stand-in killed by the escalation")

# Remediation 6: a call that never started used to leave the window mid-call,
# with the controls locked and the journal showing a call that never ran.
real_start = window.runner.start
window.runner.start = lambda *args: False
started = window._start(["-k"], "presence", "Detectare programator")
window.runner.start = real_start
assert started is False, "a call that never started reported success"
assert window.expect is None, "a failed start left an operation in flight"
assert window.status.text() == "Pregatit.", window.status.text()
assert window.read_button.isEnabled(), "the controls stayed locked"

absent = mg.MiniproGui(MISSING_BINARY)
absent.show()
code, cancelled = settle(absent.runner) or (None, False)
assert code == 127, code
assert absent.expect is None, "a refused engine left an operation in flight"
assert "esuata" in absent.status.text(), absent.status.text()
assert absent.read_button.isEnabled(), "the controls stayed locked"
absent.close()
mg.QtWidgets.QApplication.processEvents()
print("refused start reported:", absent.status.text())

# The journal must also survive the ordinary calls: every record the engine
# wrote stays visible, and one chip record is shown exactly once.
assert run(window.runner, window.do_list_devices), "the list call hung"
assert window.devices.count() > 100, window.devices.count()
choose(window.devices, "AT27C512R@DIP28")
assert run(window.runner, window.do_chip_info), "the chip info call hung"
voltages = [window.vpp.itemText(i) for i in range(window.vpp.count())]
assert len(voltages) > 1, voltages
assert run(window.runner, window.do_id), "the id call hung"
assert window.status.text().startswith("Citire ID "), window.status.text()
journal = window.log.toPlainText()
assert journal.count("Available VPP voltages") == 1, "chip info was shown twice"
for earlier in (" -k", " -V", " -l", " -d"):
    assert earlier in journal, (earlier, journal[-400:])
print("journal kept:", len(journal.splitlines()), "lines")

window.close()
mg.QtWidgets.QApplication.processEvents()
assert not window.runner.running(), "an engine call outlived the window"
assert not survivors("fakeminipro"), survivors("fakeminipro")
print("REGRESSION OK")

