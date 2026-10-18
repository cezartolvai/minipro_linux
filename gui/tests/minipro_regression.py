# This file is a part of the minipro project
# https://gitlab.com/DavidGriffith/minipro/
#
# Minipro is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
"""Regression checks for the front-end defects that were remediated.

Each block names the defect it guards against, so a change that reintroduces
one of them fails here instead of on real hardware.  Command lines are checked
against src/main.c of the engine; the cancel, close and overwrite paths run
against stand-ins that behave like minipro, so a long or stubborn call can be
exercised with no programmer attached.

The suite is sealed on purpose.  It never builds a window over the real engine,
it never looks for a stray process by name, and every process it starts is
recorded so cleanup can only ever reach its own.  The engine database checks
are kept, but they only run when MINIPRO_TEST_ENGINE=1 asks for them by name.

    make gui-test                        sealed checks, no hardware involved
    MINIPRO_TEST_ENGINE=1 make gui-test   plus the engine database checks
"""
import atexit
import importlib.util
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
GUI_PATH = os.path.join(ROOT, "gui", "minipro_gui.py")
STUB = os.path.join(HERE, "fakeminipro.sh")
SLOW_STUB = os.path.join(HERE, "fakeminipro_slow.sh")
STUBBORN_STUB = os.path.join(HERE, "fakeminipro_stubborn.sh")
MISSING_BINARY = os.path.join(HERE, "minipro-that-does-not-exist")
ENGINE_REQUESTED = os.environ.get("MINIPRO_TEST_ENGINE") == "1"

for stand_in in (STUB, SLOW_STUB, STUBBORN_STUB):
    if not os.access(stand_in, os.X_OK):
        raise SystemExit("missing stand-in: " + stand_in)

spec = importlib.util.spec_from_file_location("mg", GUI_PATH)
mg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mg)

QtCore = mg.QtCore
QtWidgets = mg.QtWidgets
from PyQt5 import QtGui  # QCloseEvent lives here, not in the front-end

sys.argv = ["minipro_regression.py"]
APP = QtWidgets.QApplication(sys.argv)

# One private directory for everything this run writes, removed when it ends.
WORKSPACE = tempfile.mkdtemp(prefix="minipro-gui-test-")

# The window remembers the language its operator chose, so this suite has to
# choose one before its first window is built: every expectation below was
# written against the Romanian window, and the language checks switch away from
# it and back on purpose.  QSettings is pointed at the private directory first,
# so a test run never reads or writes the settings of the person running it.
QtCore.QSettings.setDefaultFormat(QtCore.QSettings.IniFormat)
QtCore.QSettings.setPath(QtCore.QSettings.IniFormat,
                         QtCore.QSettings.UserScope, WORKSPACE)
mg.store_language("ro")


def process_fields(pid):
    """Return the fields of one kernel record after its comm field, or None.

    The comm field is wrapped in parentheses and may hold spaces, so the tail
    of the record is read from after its last closing parenthesis.
    """
    try:
        with open("/proc/%d/stat" % int(pid)) as handle:
            record = handle.read()
    except OSError:
        return None
    marker = record.rfind(") ")
    if marker < 0:
        return None
    return record[marker + 2:].split()


def process_state(pid):
    """Return the state letter the kernel reports for one pid, or None."""
    fields = process_fields(pid)
    return fields[0][:1] if fields else None


def process_start_stamp(pid):
    """Return when the kernel says one pid was created, or None.

    The tail read here starts at the state field, which is the third field of
    the kernel record, so the creation time of the process - field 22 - is the
    twentieth field of that tail.  It is the only part of a pid that survives
    no matter who holds that number: a process that ended and a process the
    operating system started later carry different creation times.
    """
    fields = process_fields(pid)
    if fields is None or len(fields) < 20:
        return None
    return fields[19]


def process_belongs(pid, stamp):
    """Report whether one pid still belongs to the record made for it.

    A pid alone cannot say whether it still belongs to the process it was
    recorded for, because the operating system hands the same number to a later
    process.  Comparing the creation time taken when the pid was recorded with
    the one the kernel reports now keeps a reused pid out of reach.

    Without a recorded creation time there is nothing to compare, so the pid is
    not claimed as ours: cleanup reports what it cannot identify instead of
    signalling through a number whose owner it cannot prove.
    """
    if stamp is None:
        return False
    if not process_exists(pid):
        return False
    return process_start_stamp(pid) == stamp


def process_exists(pid):
    """Report whether one pid still belongs to a live process.

    A process that was killed but whose parent has not waited on it yet is a
    zombie: the kernel still answers for its pid, yet nothing of it runs.
    Counting one as surviving makes cleanup report a process it already stopped.
    """
    state = process_state(pid)
    if state is not None:
        return state != "Z"
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError):
        return False
    return True


def kill_process(pid, signal_number):
    """Send one signal to one pid, reporting whether the kernel accepted it."""
    try:
        os.kill(int(pid), signal_number)
    except OSError:
        return False
    return True


def direct_children(pid):
    """Return the pids whose parent is this pid."""
    done = subprocess.run(["ps", "-o", "pid=", "--ppid", str(int(pid))],
                          capture_output=True)
    return [int(word) for word in done.stdout.decode("utf-8", "replace").split()]


def descendants(pid, found=None):
    """Return every process below one pid, so no child of ours survives."""
    found = [] if found is None else found
    for child in direct_children(pid):
        if child in found:
            continue
        found.append(child)
        descendants(child, found)
    return found


class Ledger:
    """Every process this suite started, so cleanup never guesses by name.

    The suite used to sweep the process table for its own stand-in name, which
    also matched whatever else happened to look similar on the same machine.
    A ledger lets cleanup reach exactly the processes this run started.

    Each entry keeps the creation time the kernel reported for its pid at the
    moment it was recorded, so a cleanup pass can tell its own process from an
    unrelated one that later received the same number.
    """

    def __init__(self):
        self.entries = []

    @property
    def pids(self):
        """Return the numbers this ledger still holds an entry for."""
        return [pid for pid, _ in self.entries]

    def note(self, pid):
        """Record one process, with the creation time its pid was born with."""
        if pid:
            pid = int(pid)
            self.entries.append((pid, process_start_stamp(pid)))
        return pid

    def owned(self):
        """Return the entries whose pid still belongs to the process recorded."""
        return [(pid, stamp) for pid, stamp in self.entries
                if process_belongs(pid, stamp)]

    def alive(self):
        """Report the recorded pids that are still running."""
        return [pid for pid, _ in self.entries if process_exists(pid)]

    def claim_children(self):
        """Record every child of a process this ledger owns, under its own stamp.

        Children are found by walking the process table, which names a pid but
        says nothing about who owns it.  Recording each one under the creation
        time the kernel gave it means the same ownership test guards a child as
        guards the process a test started.
        """
        for pid, _ in self.owned():
            for child in descendants(pid):
                if child not in self.pids:
                    self.entries.append((child, process_start_stamp(child)))

    def reap(self):
        """Stop the recorded processes that are still ours, and only those.

        Ownership is re-checked immediately before every signal, and an entry is
        dropped as soon as its pid stops belonging to the process it was
        recorded for, so a cleanup pass can never signal a number the operating
        system handed to an unrelated process.
        """
        self.claim_children()
        for pid, _ in self.owned():
            kill_process(pid, signal.SIGTERM)
        self.prune()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            self.claim_children()
            remaining = self.owned()
            if not remaining:
                break
            for pid, _ in remaining:
                kill_process(pid, signal.SIGKILL)
            self.prune()
            time.sleep(0.05)
        return [pid for pid, _ in self.owned()]

    def unidentified(self):
        """Report the recorded pids whose owner this ledger cannot prove."""
        return [pid for pid, stamp in self.entries
                if stamp is None and process_exists(pid)]

    def prune(self):
        """Forget every entry this run can no longer signal.

        An entry stays while its pid still belongs to the process recorded for
        it, and also while its owner simply cannot be proved: dropping an
        unidentifiable process would hide a survivor instead of reporting it.
        """
        kept = []
        for pid, stamp in self.entries:
            if process_belongs(pid, stamp):
                kept.append((pid, stamp))
            elif stamp is None and process_exists(pid):
                kept.append((pid, stamp))
        self.entries = kept


LEDGER = Ledger()


def cleanup_workspace():
    """Remove what this run wrote, whatever way the run ended."""
    leftovers = LEDGER.reap()
    if leftovers:
        sys.stderr.write("processes this suite could not stop: %s\n" % leftovers)
    unidentified = LEDGER.unidentified()
    if unidentified:
        sys.stderr.write("processes this suite could not identify: %s\n"
                         % unidentified)
    shutil.rmtree(WORKSPACE, ignore_errors=True)


atexit.register(cleanup_workspace)


class Dialogs:
    """Answer every dialog a test triggers, and record what it asked.

    A test must never block on a modal dialog, and it must be able to prove
    that a dangerous action either asked first or refused to run at all.
    """

    def __init__(self, answer=None):
        self.answer = (QtWidgets.QMessageBox.Yes if answer is None else answer)
        self.queued = []
        self.asked = []

    def queue(self, *answers):
        """Answer the next dialogs in this exact order, one per dialog.

        A two-step confirmation must be tested both ways: refused at the first
        question and refused at the last one.  One fixed answer cannot do that.
        """
        self.queued.extend(answers)

    def install(self):
        """Replace the dialogs the front-end can open with answered ones."""
        box = QtWidgets.QMessageBox
        self.original = (box.question, box.warning, box.information,
                         box.critical)
        box.question = staticmethod(self._stand_in("question"))
        box.warning = staticmethod(self._stand_in("warning"))
        box.information = staticmethod(self._stand_in("information"))
        box.critical = staticmethod(self._stand_in("critical"))

    def _stand_in(self, kind):
        """Build one answered dialog, and remember that it was asked."""
        def answered(parent, title, text, *args, **kwargs):
            self.asked.append((kind, title, str(text)))
            if self.queued:
                return self.queued.pop(0)
            return self.answer
        return answered

    def restore(self):
        """Give the real dialogs back to the front-end."""
        box = QtWidgets.QMessageBox
        (box.question, box.warning, box.information,
         box.critical) = self.original

    def titles(self, kind=None):
        """Report the titles of the dialogs opened during one test."""
        if kind is None:
            return [entry[1] for entry in self.asked]
        return [entry[1] for entry in self.asked if entry[0] == kind]


class Completions(QtCore.QObject):
    """Remember how each engine call this test started actually ended."""

    def __init__(self, runner):
        super().__init__()
        self.runner = runner
        self.records = []
        runner.done.connect(self._record)

    def _record(self, exit_code, stdout_text):
        self.records.append((int(exit_code), bool(self.runner.cancelled)))



class WindowCase(unittest.TestCase):
    """One front-end window over a stand-in, cleaned up whatever happens."""

    binary = STUB

    def setUp(self):
        self.dialogs = Dialogs()
        self.dialogs.install()
        self.addCleanup(self.dialogs.restore)
        self.workdir = tempfile.mkdtemp(prefix="case-", dir=WORKSPACE)
        self.addCleanup(shutil.rmtree, self.workdir, True)
        self.dump = os.path.join(self.workdir, "dump.bin")
        self.window = self.make_window(self.binary)

    def tearDown(self):
        survivors = LEDGER.reap()
        self.assertEqual(survivors, [], "a stand-in this test started survived")

    def make_window(self, binary):
        """Build one window, wait for its startup call, register its cleanup."""
        window = mg.MiniproGui(binary)
        window.show()
        window.completions = Completions(window.runner)
        self.addCleanup(self.close_window, window)
        self.assertIsNotNone(self.settle(window), "the startup call never ended")
        del self.dialogs.asked[:]
        return window

    def close_window(self, window):
        """Stop whatever this window is still running, then let it go."""
        self.dialogs.answer = QtWidgets.QMessageBox.Yes
        if window.runner.running():
            LEDGER.note(window.runner.process_id())
            window.runner.cancel()
            if not window._wait_for_stop(2000):
                window.runner.force_kill()
                window._wait_for_stop(2000)
        for _ in range(20):
            QtWidgets.QApplication.processEvents()
        window.close()
        QtWidgets.QApplication.processEvents()

    def settle(self, window, timeout_ms=20000):
        """Pump the event loop until the running engine call reports."""
        records = window.completions.records
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            QtWidgets.QApplication.processEvents()
            if records:
                return records.pop(0)
            time.sleep(0.02)
        return None

    def act(self, trigger, timeout_ms=20000):
        """Trigger one action and report how the call it started ended."""
        del self.window.completions.records[:]
        trigger()
        LEDGER.note(self.window.runner.process_id())
        return self.settle(self.window, timeout_ms)

    def start(self, trigger, pump_ms=400):
        """Trigger one action and return while its call is still running."""
        del self.window.completions.records[:]
        trigger()
        LEDGER.note(self.window.runner.process_id())
        deadline = time.monotonic() + pump_ms / 1000.0
        while time.monotonic() < deadline:
            QtWidgets.QApplication.processEvents()
            time.sleep(0.01)

    def answer(self, answer):
        """Choose the answer every dialog will get from now on."""
        self.dialogs.answer = answer

    def queue_answers(self, *answers):
        """Answer the next dialogs in this exact order, one per dialog."""
        self.dialogs.queue(*answers)

    def watch_starts(self):
        """Return the list that records every call this window hands to the engine.

        A call that already finished and a call that never started look exactly
        alike once the runner has cleaned itself up, so a refusal test has to
        record the handover to the engine itself and not the state left behind.
        """
        started = []
        runner = self.window.runner
        handed_over = self.window.runner.start

        def watched(binary, args, label, home=None):
            started.append(list(args))
            return handed_over(binary, args, label, home)

        runner.start = watched
        return started

    def titles(self, kind=None):
        """Report the dialogs the front-end opened during this test."""
        return self.dialogs.titles(kind)

    def choose_chip(self, name="W25Q16@SOIC8"):
        """Give this window a chip, since no action runs without one."""
        self.window.devices.setEditText(name)

    def reset(self, window=None):
        """Return every control of one window to its untouched state."""
        window = window or self.window
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
        window.file.setText(self.dump)


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



CHIP_RECORD = """Found 1 device(s) found.
Name:           W25Q16@SOIC8
Device ID:      EF4018
Available SPI clock frequencies [MHz]:
5, 33.33
Available VPP voltages [V]:
5, 12.5
Available VDD write voltages [V]:
3.3
Available VCC verify voltages [V]:
3.3, 5
"""

MAIN_C = os.path.join(ROOT, "src", "main.c")


def main_c_long_options():
    """Return every long option the engine source says it accepts."""
    with open(MAIN_C, encoding="utf-8", errors="replace") as handle:
        source = handle.read()
    table = re.search(r"long_options\[\] = \{(.*?)\n\};", source, re.S)
    if table is None:
        raise SystemExit("main.c no longer declares its long option table")
    names = re.findall(r'\{\s*"([a-z_]+)"\s*,\s*(?:no|required|optional)_argument',
                       table.group(1))
    return set("--" + name for name in names)



def window_journal(window):
    """Return everything one window has shown in its journal."""
    return window.log.toPlainText()


class CommandLineChecks(WindowCase):
    """The front-end may only ever build a command line minipro accepts.

    Every flag below was checked against src/main.c of the engine: a flag the
    engine rejects, a flag sent for an action that rejects it, or a wrong case
    for an option letter all show up here at the argument level instead of on
    hardware.
    """

    def test_a_plain_read_sends_only_flags_main_c_accepts(self):
        self.choose_chip()
        arguments = self.window._op_args("read", "W25Q16@SOIC8", self.dump)
        self.assertEqual(arguments[:2], ["-p", "W25Q16@SOIC8"])
        self.assertEqual(arguments[2:4], ["-r", self.dump])
        for argument in arguments:
            self.assertFalse(argument.startswith("--"),
                             "a default read sent %r" % argument)

    def test_every_long_option_the_front_end_sends_is_one_main_c_declares(self):
        declared = main_c_long_options()
        invented = {option for option, _label in mg.CONFIG_SECTIONS if option}
        invented |= {"--vpp", "--vdd", "--vcc", "--spi_clock", "--pulse",
                     "--address"}
        missing = invented - declared
        self.assertEqual(missing, set(),
                         "main.c never declares %s" % sorted(missing))

    def test_a_config_filter_forces_the_region_main_c_actually_programs(self):
        self.choose_chip()
        pick(self.window.cfg_section, "--lock")
        arguments = self.window._op_args("read", "W25Q16@SOIC8", None)
        self.assertIn("--lock", arguments)
        self.assertEqual(arguments.count("-c"), 1,
                         "a config filter sent two memory regions")
        position = arguments.index("-c")
        self.assertEqual(arguments[position + 1], "config")

    def test_a_config_filter_overrides_a_conflicting_memory_region(self):
        self.choose_chip()
        pick(self.window.memory, "data")
        pick(self.window.cfg_section, "--lock")
        arguments = self.window._op_args("read", "W25Q16@SOIC8")
        position = arguments.index("-c")
        self.assertEqual(arguments[position + 1], "config")
        self.assertIn(mg.tr("journal.config_forces"),
                      window_journal(self.window))

    def test_a_memory_region_is_sent_verbatim_as_main_c_expects_it(self):
        self.choose_chip()
        pick(self.window.memory, "calibration")
        arguments = self.window._op_args("read", "W25Q16@SOIC8", self.dump)
        self.assertEqual(arguments[2:4], ["-c", "calibration"])

    def test_database_queries_carry_the_selected_database(self):
        self.assertEqual(self.window._db_args(["-l"]), ["-q", "tl866a", "-l"])
        pick(self.window.db_filter, "t48")
        self.assertEqual(self.window._db_args(["-l"]), ["-q", "t48", "-l"])

    def test_chip_search_uses_the_option_main_c_reads_as_a_name(self):
        self.window.search.setText("24C02")
        ended = self.act(self.window.do_search)
        self.assertIsNotNone(ended, "the search never ended")
        self.assertEqual(ended[0], 0)
        self.assertIn("-L 24C02", window_journal(self.window))

    def test_chip_search_never_becomes_a_database_list(self):
        self.window.search.setText("AT28C256")
        self.act(self.window.do_search)
        journal = window_journal(self.window)
        self.assertIn("-L AT28C256", journal)
        self.assertNotIn(" -l ", journal, "a search also listed the database")

    def test_overrides_are_sent_only_when_the_advanced_box_is_open(self):
        self.window.apply_chip_info(CHIP_RECORD)
        choose(self.window.vpp, "12.5")
        closed = self.window._op_args("read", "W25Q16@SOIC8", self.dump)
        self.assertNotIn("--vpp", closed)
        self.window.advanced.setChecked(True)
        opened = self.window._op_args("read", "W25Q16@SOIC8", self.dump)
        self.assertIn("--vpp", opened)
        self.assertEqual(opened[opened.index("--vpp") + 1], "12.5")


    def test_skip_id_is_sent_only_for_the_actions_main_c_allows_it_for(self):
        self.choose_chip()
        self.window.advanced.setChecked(True)
        self.window.skip_id.setChecked(True)
        for action in mg.SKIP_ID_ACTIONS:
            self.assertIn("-x", self.window._op_args(action, "W25Q16@SOIC8"),
                          "main.c allows -x for %s" % action)
        for action in ("write", "erase", "id"):
            self.assertNotIn("-x", self.window._op_args(action, "W25Q16@SOIC8"),
                             "main.c rejects -x for %s" % action)
        self.assertIn("-x nu este permis", window_journal(self.window))

    def test_write_confirmation_names_every_option_being_sent(self):
        started = self.watch_starts()
        self.choose_chip()
        with open(self.dump, "wb") as handle:
            handle.write(b"\x00" * 64)
        self.window.advanced.setChecked(True)
        self.window.apply_chip_info(CHIP_RECORD)
        choose(self.window.vpp, "12.5")
        self.window.unprotect.setChecked(True)
        self.window.skip_erase.setChecked(True)
        self.window.file.setText(self.dump)
        self.answer(QtWidgets.QMessageBox.No)
        self.window.do_write()
        asked = [text for kind, title, text in self.dialogs.asked
                 if title == "Confirmare scriere"]
        self.assertEqual(len(asked), 1, "the write skipped its confirmation")
        self.assertIn("--vpp 12.5", asked[0])
        self.assertIn("-u", asked[0])
        self.assertIn("-e", asked[0], "the dialog hid an option being sent")
        self.assertEqual(started, [],
                         "a refused write still reached the engine")

    def test_write_confirmation_names_the_region_the_write_targets(self):
        # The region and the format decide where the bytes land, so they belong
        # in the confirmation just like the advanced options do.
        started = self.watch_starts()
        self.choose_chip()
        with open(self.dump, "wb") as handle:
            handle.write(b"\x00" * 64)
        pick(self.window.memory, "data")
        pick(self.window.format, "ihex")
        self.window.file.setText(self.dump)
        self.answer(QtWidgets.QMessageBox.No)
        self.window.do_write()
        asked = [text for kind, title, text in self.dialogs.asked
                 if title == "Confirmare scriere"]
        self.assertEqual(len(asked), 1, "the write skipped its confirmation")
        self.assertIn("-c data", asked[0], "the dialog hid the region being written")
        self.assertIn("-f ihex", asked[0], "the dialog hid the format being sent")
        self.assertEqual(started, [],
                         "a refused write still reached the engine")

    def test_erase_confirmation_says_the_region_it_shows_is_not_a_limit(self):
        # main.c never reads the chosen page when it erases, so naming the
        # region is only honest when the dialog also says it does not narrow
        # the erase down to that region.
        started = self.watch_starts()
        self.choose_chip()
        pick(self.window.memory, "data")
        self.answer(QtWidgets.QMessageBox.No)
        self.window.do_erase()
        asked = [text for kind, title, text in self.dialogs.asked
                 if title == "Confirmare stergere"]
        self.assertEqual(len(asked), 1, "an erase ran without its confirmation")
        self.assertIn("Stergerea distruge tot continutul", asked[0])
        self.assertIn("-c data", asked[0],
                      "the dialog hid the region selection it shows")
        self.assertIn("nu limiteaza stergerea", asked[0],
                      "the dialog let a region selection read as a limit")
        self.assertEqual(started, [],
                         "a refused erase still reached the engine")

    def test_a_confirmation_never_names_an_option_the_action_refuses(self):
        started = self.watch_starts()
        self.choose_chip()
        with open(self.dump, "wb") as handle:
            handle.write(b"\x00" * 64)
        self.window.advanced.setChecked(True)
        self.window.skip_id.setChecked(True)
        self.window.file.setText(self.dump)
        self.answer(QtWidgets.QMessageBox.No)
        self.window.do_write()
        asked = [text for kind, title, text in self.dialogs.asked
                 if title == "Confirmare scriere"]
        self.assertEqual(len(asked), 1)
        # Only the option lines are checked: the dump path this test was given
        # is a random name that can hold any two characters by accident.
        options = " ".join(line for line in asked[0].splitlines()
                           if line.startswith("Optiun"))
        self.assertNotIn("-x", options,
                         "the dialog named an option main.c never accepts here")
        self.assertIn("Nu citi ID-ul", options,
                      "the dialog hid the option it had silently dropped")
        self.assertEqual(started, [],
                         "a refused write still reached the engine")

    def test_the_summary_names_the_id_option_where_main_c_accepts_it(self):
        # The same box feeds both kinds of action, so the summary has to name
        # -x for the actions that really get it and stay silent for the rest.
        self.choose_chip()
        self.window.advanced.setChecked(True)
        self.window.skip_id.setChecked(True)
        for action in mg.SKIP_ID_ACTIONS:
            self.assertIn("-x", self.window._override_summary(action),
                          "the summary hid the -x _op_args really sends for %s"
                          % action)
        for action in ("write", "erase", "id"):
            self.assertNotIn("-x", self.window._override_summary(action),
                             "the summary named -x for %s" % action)

    def test_the_summary_ignores_a_box_behind_a_closed_advanced_group(self):
        # _op_args only reads these boxes while the advanced group is open, so
        # a checked box behind a closed group is not an option being dropped
        # and the confirmation must not mention it either.
        started = self.watch_starts()
        self.choose_chip()
        with open(self.dump, "wb") as handle:
            handle.write(b"\x00" * 64)
        self.window.skip_id.setChecked(True)
        self.window.file.setText(self.dump)
        self.answer(QtWidgets.QMessageBox.No)
        self.window.do_write()
        asked = [text for kind, title, text in self.dialogs.asked
                 if title == "Confirmare scriere"]
        self.assertEqual(len(asked), 1)
        options = " ".join(line for line in asked[0].splitlines()
                           if line.startswith("Optiun"))
        self.assertNotIn("-x", options,
                         "the summary named an option a closed group never sends")
        self.assertNotIn("Nu citi ID-ul", options,
                         "the summary named an option that was never asked for")
        self.assertEqual(started, [],
                         "a refused write still reached the engine")

    def test_the_command_line_is_quoted_exactly_as_the_engine_sees_it(self):
        # The journal line is the command line this front-end really runs, so
        # quoting must follow shell rules: only what a shell would split or
        # expand gets quoted, and quoting it must not change the argument.
        self.assertEqual(
            mg.display_command("/tmp/minipro", ["-p", "AT28C256@DIP28", "-E"]),
            "/tmp/minipro -p AT28C256@DIP28 -E")
        self.assertEqual(
            mg.display_command("/tmp/minipro", ["-r", "/tmp/un dump.bin"]),
            "/tmp/minipro -r '/tmp/un dump.bin'")
        self.assertEqual(
            mg.display_command("/tmp/minipro", ["-r", "dump$HOME.bin"]),
            "/tmp/minipro -r 'dump$HOME.bin'")
        self.assertEqual(
            shlex.split(mg.display_command("/tmp/minipro",
                                                ["-r", "/tmp/un dump.bin"]))[2],
            "/tmp/un dump.bin")


def settle_recorder(recorder, timeout_ms=20000):
    """Pump the event loop until one engine call has reported how it ended."""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        QtWidgets.QApplication.processEvents()
        if recorder.records:
            return recorder.records.pop(0)
        time.sleep(0.02)
    return None


def close_window_quietly(window):
    """Stop whatever one window is still running, then let the window go."""
    if window.runner.running():
        LEDGER.note(window.runner.process_id())
        window.runner.cancel()
        if not window._wait_for_stop(2000):
            window.runner.force_kill()
            window._wait_for_stop(2000)
    for _ in range(20):
        QtWidgets.QApplication.processEvents()
    window.close()
    QtWidgets.QApplication.processEvents()


class FileSafetyChecks(WindowCase):
    """A dump path is something the engine overwrites, so it is guarded first.

    minipro opens its read target with "wb", which truncates whatever is
    already there.  The front-end used to accept a directory as a dump file and
    to overwrite an existing dump without ever asking.
    """

    def choose_chip(self, name="W25Q16@SOIC8"):
        """Give this window a chip, since no action runs without one."""
        self.window.devices.setEditText(name)

    def journal(self):
        """Return everything this window has shown in its journal."""
        return self.window.log.toPlainText()

    def setUp(self):
        """Give this window a chip and a dump: every check here is about one."""
        super().setUp()
        self.choose_chip()
        self.window.file.setText(self.dump)

    def test_a_directory_is_refused_before_the_engine_is_asked_for_it(self):
        started = self.watch_starts()
        self.window.file.setText(self.workdir)
        self.start(self.window.do_read, 300)
        self.assertIn("Fisier invalid", self.titles("warning"))
        self.assertIn("Fisier invalid", self.journal())
        self.assertEqual(started, [],
                         "a directory was handed to the engine as a dump file")

    def test_a_read_never_overwrites_an_existing_dump_without_asking(self):
        started = self.watch_starts()
        with open(self.dump, "wb") as handle:
            handle.write(b"\xa5" * 64)
        self.act(self.window.do_read)
        asked = [title for kind, title, text in self.dialogs.asked]
        self.assertIn("Confirmare suprascriere", asked)
        # This stand-in never touches the file, so what proves the read ran is
        # the command line it was handed, not the bytes left on disk.
        self.assertEqual(started, [["-p", "W25Q16@SOIC8", "-r", self.dump]],
                         "the read that was asked about never reached the engine")

    def test_a_declined_overwrite_leaves_the_existing_dump_untouched(self):
        started = self.watch_starts()
        with open(self.dump, "wb") as handle:
            handle.write(b"\xa5" * 64)
        self.answer(QtWidgets.QMessageBox.No)
        self.start(self.window.do_read, 300)
        self.assertIn("Confirmare suprascriere", self.titles("question"))
        self.assertEqual(os.path.getsize(self.dump), 64)
        self.assertIn("Citirea va inlocui", self.journal())
        self.assertEqual(started, [],
                         "the engine ran after the operator refused the overwrite")

    def test_a_dump_the_engine_must_read_is_required_to_exist_first(self):
        started = self.watch_starts()
        self.window.file.setText(os.path.join(self.workdir, "absent.bin"))
        self.start(self.window.do_verify, 300)
        self.assertIn("Fisier lipsa", self.titles("warning"))
        self.assertIn("aceasta operatie il citeste", self.journal())
        self.assertEqual(started, [],
                         "the engine was asked to read a dump that does not exist")

    def test_an_undeclared_dump_path_stops_the_action_before_the_engine_runs(self):
        started = self.watch_starts()
        self.window.file.setText("")
        self.start(self.window.do_write, 300)
        self.assertIn("Fisier lipsa", self.titles("warning"))
        self.assertEqual(started, [],
                         "an action with no dump path still reached the engine")

    def test_an_erase_refused_at_the_first_question_never_reaches_the_engine(self):
        started = self.watch_starts()
        self.answer(QtWidgets.QMessageBox.No)
        self.start(self.window.do_erase, 300)
        self.assertEqual(self.titles("warning"), ["Confirmare stergere"],
                         "a refused first answer still asked the second question")
        self.assertIn("Stergerea distruge tot continutul", self.journal())
        self.assertEqual(started, [], "an erase reached the engine after a No")

    def test_an_erase_refused_at_the_final_warning_never_reaches_the_engine(self):
        started = self.watch_starts()
        self.queue_answers(QtWidgets.QMessageBox.Yes, QtWidgets.QMessageBox.No)
        self.start(self.window.do_erase, 300)
        self.assertEqual(self.titles("warning"),
                         ["Confirmare stergere", "Confirmare finala"],
                         "the erase never asked its second question")
        self.assertEqual(started, [],
                         "an erase reached the engine after its last answer was No")

    def test_an_erase_reaches_the_engine_only_after_two_acceptances(self):
        started = self.watch_starts()
        self.answer(QtWidgets.QMessageBox.Yes)
        ended = self.act(self.window.do_erase)
        self.assertIsNotNone(ended, "the confirmed erase never reported back")
        self.assertEqual(ended[0], 0)
        self.assertEqual(self.titles("warning"),
                         ["Confirmare stergere", "Confirmare finala"],
                         "an erase that ran did not ask twice")
        self.assertEqual(started, [["-p", "W25Q16@SOIC8", "-E"]],
                         "a confirmed erase did not reach the engine exactly once")



class ChipContextChecks(WindowCase):
    """Chip values must belong to the chip that produced them.

    The override lists used to keep the values of whichever chip was queried
    last, so an operator could program one chip with the clock and voltages of
    another.  The values are now remembered as belonging to a named chip and
    dropped as soon as that chip is no longer the one selected.
    """

    def journal(self):
        """Return everything one window has shown in its journal."""
        return self.window.log.toPlainText()

    def test_a_chip_record_fills_the_lists_it_names_for_that_chip(self):
        self.window.apply_chip_info(CHIP_RECORD)
        self.assertEqual(self.window.devices.currentText(), "W25Q16@SOIC8")
        self.assertEqual(self.window.chip_overrides_for, "W25Q16@SOIC8")
        self.assertEqual(self.window.vpp.count(), 3)
        self.assertEqual(self.window.vpp.itemText(0), mg.implicit_text())
        self.assertIn("12.5", self.window.vpp.itemText(2))
        # The record names the chip's own values; it does not pick one for the
        # operator, so a read still lets minipro use the database values.
        self.assertEqual(self.window.vpp.currentText(), mg.implicit_text())

    def test_a_plain_read_never_sends_a_value_borrowed_from_another_chip(self):
        self.window.apply_chip_info(CHIP_RECORD)
        self.window.devices.setEditText("AT28C256@DIP28")
        arguments = self.window._op_args("read", "AT28C256@DIP28", self.dump)
        self.window.advanced.setChecked(True)
        arguments = self.window._op_args("read", "AT28C256@DIP28", self.dump)
        self.assertNotIn("--vpp", arguments)
        self.assertNotIn("--spi_clock", arguments)
        self.assertIn("Suprascrierile cipului au fost resetate", self.journal())

    def test_the_chip_a_record_names_stays_selected_after_it_is_applied(self):
        self.window.apply_chip_info(CHIP_RECORD)
        self.assertEqual(self.window.selected_device(), "W25Q16@SOIC8")
        self.window.advanced.setChecked(True)
        # The values the record named are still the ones this chip offered, so
        # picking one of them is an override of this chip and not of another.
        choose(self.window.spi_clock, "33.33")
        arguments = self.window._op_args("read", "W25Q16@SOIC8", self.dump)
        self.assertIn("--spi_clock", arguments)
        self.assertEqual(arguments[arguments.index("--spi_clock") + 1], "33.33")

    def test_a_database_change_invalidates_the_values_it_could_no_longer_supply(self):
        self.window.apply_chip_info(CHIP_RECORD)
        self.assertEqual(self.window.spi_clock.count(), 3)
        pick(self.window.db_filter, "t48")
        self.assertEqual(self.window.spi_clock.count(), 1)
        self.assertIsNone(self.window.chip_overrides_for)
        self.assertIn("baza de date selectata s-a schimbat", self.journal())

    def test_an_empty_listing_invalidates_the_chip_it_no_longer_offers(self):
        self.window.apply_chip_info(CHIP_RECORD)
        self.window.apply_device_list("")
        self.assertEqual(self.window.devices.count(), 0)
        self.assertIsNone(self.window.chip_overrides_for)
        self.assertIn("Niciun cip gasit in baza de date selectata.", self.journal())

    def test_a_refilled_listing_that_keeps_the_chip_keeps_its_values(self):
        self.window.apply_chip_info(CHIP_RECORD)
        self.window.apply_device_list("W25Q16@SOIC8\nAT28C256@DIP28\n")
        self.assertEqual(self.window.chip_overrides_for, "W25Q16@SOIC8")
        self.assertEqual(self.window.vpp.count(), 3)


class ResultChecks(WindowCase):
    """A finished call must report what actually happened, not what was asked.

    The front-end used to report success for a call the engine refused, and it
    kept the previous listing on screen after a call that produced none.
    """

    def journal(self):
        """Return everything one window has shown in its journal."""
        return self.window.log.toPlainText()

    def test_a_failing_call_is_reported_as_a_failure_with_its_code(self):
        self.choose_chip()
        with open(self.dump, "wb") as handle:
            handle.write(b"\xa5" * 64)
        self.window.file.setText(self.dump)
        os.environ["FAKE_MINIPRO_EXIT"] = "1"
        self.addCleanup(os.environ.pop, "FAKE_MINIPRO_EXIT", None)
        ended = self.act(self.window.do_verify)
        self.assertIsNotNone(ended, "the engine call never reported back")
        self.assertEqual(ended[0], 1)
        self.assertIn("esuata, cod de iesire 1", self.journal())

    def test_a_cancelled_call_is_reported_as_cancelled_and_not_as_success(self):
        self.choose_chip()
        os.environ["FAKE_MINIPRO_HANG"] = "1"
        self.addCleanup(os.environ.pop, "FAKE_MINIPRO_HANG", None)
        self.start(self.window.do_read)
        self.assertTrue(self.window.runner.running(), "the call already ended")
        self.window.do_cancel()
        ended = self.settle(self.window)
        self.assertIsNotNone(ended, "the cancelled call never reported back")
        self.assertTrue(ended[1], "a cancelled call was not reported as cancelled")
        self.assertIn("anulata", self.journal())

    def test_a_failed_listing_says_the_views_come_from_an_earlier_call(self):
        self.act(self.window.do_list_devices)
        self.assertEqual(self.window.devices.count(), 3,
                         "the listing this stand-in produced never reached the view")
        os.environ["FAKE_MINIPRO_EXIT"] = "1"
        self.addCleanup(os.environ.pop, "FAKE_MINIPRO_EXIT", None)
        self.window.search.setText("nimic")
        ended = self.act(self.window.do_search)
        self.assertIsNotNone(ended, "the search never ended")
        self.assertEqual(ended[0], 1)
        self.assertIn("Vederile ramane cele ale unei operatii anterioare",
                      self.journal())
        self.assertEqual(self.window.devices.count(), 3,
                         "a failed listing threw away the views it never produced")



class ProcessChecks(WindowCase):
    """A running engine call must be cancellable, and cancellable twice.

    minipro can sit in a USB transfer that ignores one signal, so a cancel that
    only ever sends one signal leaves the operator with a window that cannot be
    closed and a call that cannot be stopped.
    """

    binary = SLOW_STUB

    def journal(self):
        """Return everything one window has shown in its journal."""
        return self.window.log.toPlainText()

    def test_a_cancel_reaches_a_call_that_is_still_running(self):
        self.window.devices.setEditText("W25Q16@SOIC8")
        self.start(self.window.do_read)
        self.assertTrue(self.window.runner.running(), "the call already ended")
        self.window.do_cancel()
        ended = self.settle(self.window)
        self.assertIsNotNone(ended, "the cancelled call never reported back")
        self.assertTrue(ended[1], "a cancelled call was not marked cancelled")
        self.assertFalse(self.window.runner.running(),
                         "a cancelled call was left running")

    def test_a_repeated_cancel_escalates_once_and_never_more_than_once(self):
        self.window.devices.setEditText("W25Q16@SOIC8")
        self.start(self.window.do_read)
        self.window.do_cancel()
        armed = self.window.runner._kill.remainingTime()
        self.assertGreater(armed, 0, "the cancel never armed its escalation")
        self.window.do_cancel()
        self.window.do_cancel()
        self.assertGreater(self.window.runner._kill.remainingTime(), 0,
                           "a repeated cancel disarmed the escalation")
        ended = self.settle(self.window)
        self.assertIsNotNone(ended, "the call never reported back")
        self.assertTrue(ended[1])

    def test_a_cancel_is_reported_as_nothing_to_cancel_when_nothing_runs(self):
        self.window.do_cancel()
        self.assertIn("Nu exista nicio operatie in desfasurare.",
                      self.window.status.text())

    def test_a_second_action_cannot_displace_the_call_that_is_running(self):
        self.window.devices.setEditText("W25Q16@SOIC8")
        self.start(self.window.do_read)
        self.window.do_id()
        self.assertIn("Proces in desfasurare", self.titles("information"))
        self.assertTrue(self.window.runner.running(),
                        "a second action displaced the running call")

    def test_a_cancelled_call_leaves_the_controls_usable_again(self):
        self.window.devices.setEditText("W25Q16@SOIC8")
        self.start(self.window.do_read)
        self.window.do_cancel()
        self.settle(self.window)
        self.assertTrue(self.window.devices.isEnabled(),
                        "the controls stayed locked after a cancelled call")
        self.assertEqual(self.window.status.text(), "Operatie anulata.")

    def test_a_process_that_already_ended_is_never_signalled_again(self):
        """A pid the kernel stopped answering for must not be signalled twice.

        The operating system reuses a pid once its process is gone, so a ledger
        that kept finished pids could later signal a process this suite never
        started.
        """
        started = subprocess.Popen([sys.executable, STUB],
                                   stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        LEDGER.note(started.pid)
        started.communicate(b"")
        self.assertEqual(LEDGER.reap(), [])
        self.assertEqual(LEDGER.pids, [])


class CloseChecks(WindowCase):
    """Closing must never orphan a running engine call.

    The window used to close while minipro was still writing, which left the
    call running with no owner and the dump file half written.
    """

    binary = SLOW_STUB

    def close_attempt(self, answer):
        """Send one close event to a running window and report what it did."""
        self.answer(answer)
        event = QtGui.QCloseEvent()
        self.window.closeEvent(event)
        QtWidgets.QApplication.processEvents()
        return event

    def test_closing_a_running_window_keeps_it_open_when_asked_to(self):
        self.window.devices.setEditText("W25Q16@SOIC8")
        self.start(self.window.do_read)
        event = self.close_attempt(QtWidgets.QMessageBox.No)
        self.assertFalse(event.isAccepted(),
                         "a refused close still closed the window")
        self.assertTrue(self.window.isVisible(),
                        "a refused close hid a window with a running call")
        self.assertTrue(self.window.runner.running(),
                        "a refused close dropped its running call")

    def test_closing_a_running_window_stops_the_engine_before_it_closes(self):
        self.window.devices.setEditText("W25Q16@SOIC8")
        self.start(self.window.do_read)
        event = self.close_attempt(QtWidgets.QMessageBox.Yes)
        self.assertTrue(event.isAccepted(), "a confirmed close did not close")
        self.assertFalse(self.window.runner.running(),
                         "the window closed over a running engine call")
        self.assertEqual(LEDGER.alive(), [],
                         "the engine call outlived the window that started it")

    def test_a_window_with_nothing_running_closes_without_asking(self):
        event = self.close_attempt(QtWidgets.QMessageBox.No)
        self.assertTrue(event.isAccepted())
        self.assertEqual(self.titles(), [], "an idle window asked before closing")

    def test_a_refused_close_leaves_the_window_usable_for_the_call_it_kept(self):
        self.window.devices.setEditText("W25Q16@SOIC8")
        self.start(self.window.do_read)
        self.close_attempt(QtWidgets.QMessageBox.No)
        self.window.do_cancel()
        ended = self.settle(self.window)
        self.assertIsNotNone(ended, "the call the window kept never ended")
        self.assertTrue(ended[1], "the call the window kept was not cancellable")


class StubbornCloseChecks(WindowCase):
    """A call that ignores a cancel must be killed before the window is lost.

    A minipro call stuck inside a USB transaction does not react to one signal.
    The window used to close anyway, which orphaned the call and left the
    operator with no way to find it again.
    """

    binary = STUBBORN_STUB

    def test_a_call_that_ignores_a_cancel_is_killed_before_the_window_closes(self):
        self.window.devices.setEditText("W25Q16@SOIC8")
        self.start(self.window.do_read)
        self.assertTrue(self.window.runner.running(),
                        "the stubborn call already ended")
        self.answer(QtWidgets.QMessageBox.Yes)
        event = QtGui.QCloseEvent()
        self.window.closeEvent(event)
        self.assertTrue(event.isAccepted(),
                        "a stubborn call was allowed to keep the window open")
        self.assertFalse(self.window.runner.running(),
                         "a killed call was still reported as running")
        self.assertEqual(LEDGER.alive(), [],
                         "the killed engine call survived its window")

    def test_a_window_keeps_a_call_that_refuses_to_be_stopped(self):
        self.window.devices.setEditText("W25Q16@SOIC8")
        self.start(self.window.do_read)
        self.answer(QtWidgets.QMessageBox.No)
        event = QtGui.QCloseEvent()
        self.window.closeEvent(event)
        self.assertFalse(event.isAccepted(),
                         "a running call was orphaned by a refused close")
        self.assertTrue(self.window.runner.running())


class ParsingChecks(unittest.TestCase):
    """The journal must show what minipro wrote, not how it painted it.

    minipro redraws its progress bar with ANSI escapes on stderr.  The journal
    used to keep those bytes, so a finished operation left the last painted
    frame on screen instead of the record that named its result.
    """

    def test_a_painted_progress_record_keeps_its_text_and_loses_its_painting(self):
        self.assertEqual(mg.strip_ansi("\r\x1b[KReading Code...  10%\n"),
                         "\rReading Code...  10%\n")

    def test_the_percent_a_progress_record_carries_is_the_one_kept(self):
        self.assertEqual(mg.PERCENT_RE.search("Reading Code...  42%").group(1),
                         "42")

    def test_the_engine_version_is_the_one_the_engine_prints(self):
        self.assertEqual(mg.parse_version("minipro version 1.0.8 (build 7)"),
                         "1.0.8")

    def test_the_commit_a_window_shows_is_the_short_one_the_engine_reports(self):
        text = "Git commit:\t0123456789abcdef0123456789abcdef0123456789"
        self.assertEqual(mg.parse_commit(text), "0123456789")

    def test_the_chip_counts_are_read_per_programmer_as_the_engine_prints_them(self):
        text = ("TL866A/CS: 1234 devices\nTL866II+: 234 devices\n"
                "T48: 34 devices\nT56: 4 devices\nT76: 5 devices\n")
        self.assertEqual(mg.parse_chip_counts(text),
                         {"tl866a": 1234, "tl866ii": 234, "t48": 34,
                          "t56": 4, "t76": 5})

    def test_the_programmer_names_a_window_offers_are_the_ones_the_engine_has(self):
        self.assertEqual({label for _, label in mg.DB_FILTERS},
                         {"TL866A/CS", "TL866II+", "T48", "T56", "T76"})

    def test_no_programmer_connected_is_reported_as_none(self):
        self.assertIsNone(mg.parse_programmer("[No programmer found]"))

    def test_the_values_read_from_a_chip_record_are_the_ones_listed_under_it(self):
        text = ("Available SPI clock frequencies [MHz]: \n"
                "5, 33.33\n"
                "Available VPP voltages [V]: \n"
                "5, 12.5\n")
        clocks = mg.parse_available_values(text,
                                           "Available SPI clock frequencies [MHz]:")
        voltages = mg.parse_available_values(text, "Available VPP voltages [V]:")
        self.assertEqual(clocks, ("5", "33.33"))
        self.assertEqual(voltages, ("5", "12.5"))

    def test_a_chip_record_stops_reading_values_at_the_first_line_that_is_not_one(self):
        text = ("Available VCC verify voltages [V]: \n"
                "3.3, 5\n"
                "Package: SOIC8\n")
        self.assertEqual(mg.parse_available_values(
            text, "Available VCC verify voltages [V]:"), ("3.3", "5"))


class ChannelDecodingChecks(unittest.TestCase):
    """stdout and stderr are two streams, so they cannot share one decoder.

    minipro writes listings to stdout and progress to stderr.  A multi-byte
    character cut in two by a read boundary used to be finished with the bytes
    of the other channel, so any non-ASCII engine output corrupted both.
    """

    def test_a_character_split_in_stdout_is_not_finished_with_stderr_bytes(self):
        runner = mg.Runner()
        self.assertEqual(runner._decode_stdout(b"listare \xc4", False),
                         "listare ")
        self.assertEqual(runner._decode_stderr(b"citire \xc4", False),
                         "citire ")
        self.assertEqual(runner._decode_stdout(b"\x83", False), "\u0103")
        self.assertEqual(runner._decode_stderr(b"\x83", False), "\u0103")

    def test_a_character_split_in_stderr_is_reassembled_before_it_is_reported(self):
        runner = mg.Runner()
        seen = []
        runner.log_line.connect(lambda text: seen.append(text))
        encoded = "citire ă".encode("utf-8")
        runner._feed(encoded[:len(encoded) - 1])
        runner._feed(encoded[len(encoded) - 1:])
        runner._flush_pending()
        self.assertEqual(seen, ["citire \u0103"])

    def test_the_last_stdout_bytes_are_finalized_by_the_stdout_decoder(self):
        runner = mg.Runner()
        self.assertEqual(runner._decode_stdout(b"listare \xc4", False),
                         "listare ")
        self.assertEqual(runner._decode_stdout(b"", final=True), "\ufffd")
        self.assertEqual(runner._decode_stderr(b"", final=True), "")


class ChipContextDatabaseAttributionChecks(WindowCase):
    """A chip record belongs to the database it was actually read from.

    The values a chip record supplies come from the database the engine was
    called with.  The window used to compare them against whichever database
    was selected when the answer arrived, so a database chosen while the call
    ran left the record attributed to a database it was never read from.
    """

    def journal(self):
        """Return everything one window has shown in its journal."""
        return self.window.log.toPlainText()

    def test_a_record_keeps_the_database_it_was_queried_from(self):
        self.window._db_args(["-d", "W25Q16@SOIC8"])
        pick(self.window.db_filter, "t48")
        self.window.apply_chip_info(CHIP_RECORD)
        self.assertEqual(self.window.chip_overrides_from, "tl866a")

    def test_a_database_chosen_after_the_query_still_invalidates_the_record(self):
        self.window._db_args(["-d", "W25Q16@SOIC8"])
        self.window.apply_chip_info(CHIP_RECORD)
        pick(self.window.db_filter, "t48")
        self.assertIsNone(self.window.chip_overrides_for)
        self.assertIn("baza de date selectata s-a schimbat", self.journal())


class EngineLookupChecks(unittest.TestCase):
    """Which engine a window drives has to be decided before a window opens.

    The front-end used to start with whatever it guessed, so a build tree with
    its own engine and an engine on PATH produced different windows from the
    same command.
    """

    def setUp(self):
        self.workdir = tempfile.mkdtemp(prefix="lookup-", dir=WORKSPACE)
        self.addCleanup(shutil.rmtree, self.workdir, True)
        self.saved = dict(os.environ)
        self.addCleanup(self.restore_environment)
        for name in ("MINIPRO_BIN", "MINIPRO_HOME"):
            os.environ.pop(name, None)

    def restore_environment(self):
        """Give back every engine variable one test had changed."""
        for name in ("MINIPRO_BIN", "MINIPRO_HOME"):
            os.environ.pop(name, None)
            if name in self.saved:
                os.environ[name] = self.saved[name]

    def front_end_copied_into(self, directory):
        """Return the front-end copied into one directory, with its own layout."""
        target = os.path.join(directory, "minipro_gui.py")
        shutil.copy(GUI_PATH, target)
        spec = importlib.util.spec_from_file_location("private_gui", target)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_an_engine_built_beside_the_front_end_is_the_one_it_uses(self):
        shutil.copy(STUB, os.path.join(self.workdir, "minipro"))
        module = self.front_end_copied_into(self.workdir)
        self.assertEqual(module.find_binary(),
                         os.path.abspath(os.path.join(self.workdir, "minipro")))

    def test_an_environment_choice_outranks_an_engine_beside_the_front_end(self):
        shutil.copy(STUB, os.path.join(self.workdir, "minipro"))
        chosen = os.path.join(self.workdir, "alt-minipro")
        shutil.copy(STUB, chosen)
        os.environ["MINIPRO_BIN"] = chosen
        self.assertEqual(mg.find_binary(), os.path.abspath(chosen))

    def test_an_explicit_path_outranks_every_other_candidate(self):
        chosen = os.path.join(self.workdir, "alt-minipro")
        shutil.copy(STUB, chosen)
        os.environ["MINIPRO_BIN"] = os.path.join(self.workdir, "minipro")
        shutil.copy(STUB, os.path.join(self.workdir, "minipro"))
        self.assertEqual(mg.find_binary(chosen), os.path.abspath(chosen))

    def test_an_engine_found_on_path_is_reported_as_an_absolute_path(self):
        bin_directory = os.path.join(self.workdir, "bin")
        os.makedirs(bin_directory)
        shutil.copy(STUB, os.path.join(bin_directory, "minipro"))
        saved_path = os.environ["PATH"]
        self.addCleanup(self.restore_path, saved_path)
        os.environ["PATH"] = bin_directory
        module = self.front_end_copied_into(self.workdir)
        self.assertEqual(module.find_binary(),
                         os.path.abspath(os.path.join(bin_directory, "minipro")))

    def restore_path(self, saved_path):
        """Give back the search path one test had replaced."""
        os.environ["PATH"] = saved_path


    def test_a_missing_engine_is_refused_before_a_window_opens(self):
        self.assertRaises(mg.BinaryNotFound, mg.find_binary, MISSING_BINARY)

    def test_a_missing_engine_is_named_in_the_refusal(self):
        try:
            mg.find_binary(MISSING_BINARY)
        except mg.BinaryNotFound as error:
            self.assertIn("minipro", str(error))
        else:
            self.fail("a missing engine was accepted without a word")


class MissingEngineChecks(unittest.TestCase):
    """A window over an engine that is not there must say so and stop."""

    def setUp(self):
        self.dialogs = Dialogs()
        self.dialogs.install()
        self.addCleanup(self.dialogs.restore)
        self.workdir = tempfile.mkdtemp(prefix="missing-", dir=WORKSPACE)
        self.addCleanup(shutil.rmtree, self.workdir, True)

    def window_over_nothing(self):
        """Build one window over an engine that is not there.

        Its startup call is settled first: a window with a call in progress
        refuses a second one, and a test must not lean on that refusal.
        """
        window = mg.MiniproGui(MISSING_BINARY)
        window.show()
        window.completions = Completions(window.runner)
        self.addCleanup(close_window_quietly, window)
        startup = settle_recorder(window.completions)
        self.assertIsNotNone(startup, "the startup call never reported back")
        del window.completions.records[:]
        del self.dialogs.asked[:]
        return window

    def test_a_window_over_a_missing_engine_never_claims_a_call_ran(self):
        window = self.window_over_nothing()
        window.do_list_devices()
        ended = settle_recorder(window.completions)
        self.assertFalse(window.runner.running())
        self.assertIsNotNone(ended, "the window never reported how the call ended")
        # A call whose engine is not there is reported as a failure, never as
        # a success, and never as a call that produced output.
        self.assertEqual(ended, (127, False))
        self.assertIn("Nu am putut porni", window_journal(window))
        self.assertNotIn("cu succes", window_journal(window))

    def test_a_window_over_a_missing_engine_leaves_its_controls_free(self):
        window = self.window_over_nothing()
        self.assertTrue(window.devices.isEnabled())
        self.assertTrue(window.list_button.isEnabled())
        self.assertFalse(window.cancel.isEnabled(),
                         "the window offered to cancel a call that never started")
        self.assertFalse(window.runner.running(),
                         "the window stayed busy over an engine that never started")
        # The status names the failure instead of claiming a call finished.
        self.assertIn("esuata", window.status.text())





class ProcessIsolationChecks(unittest.TestCase):
    """Cleanup may only ever reach the processes this suite itself started."""

    def setUp(self):
        self.workdir = tempfile.mkdtemp(prefix="isolation-", dir=WORKSPACE)
        self.addCleanup(shutil.rmtree, self.workdir, True)

    def finish(self, process):
        """Stop one process one test started outside the ledger."""
        if process_exists(process.pid):
            kill_process(process.pid, signal.SIGKILL)
        process.wait()

    def lookalike(self):
        """Start a stand-in engine this suite never recorded anywhere."""
        copy = os.path.join(self.workdir, "fakeminipro.sh")
        shutil.copy(STUB, copy)
        foreign = subprocess.Popen([copy, "-r", "W25Q16@SOIC8"],
                                   stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL,
                                   cwd=self.workdir,
                                   env=dict(os.environ, FAKE_MINIPRO_HANG="1"))
        self.addCleanup(self.finish, foreign)
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and not process_exists(foreign.pid):
            time.sleep(0.02)
        return foreign

    def test_one_cleanup_pass_leaves_a_process_it_never_started_alone(self):
        foreign = self.lookalike()
        self.assertTrue(process_exists(foreign.pid), "the lookalike never ran")
        self.assertEqual(LEDGER.reap(), [],
                         "cleanup reported a process it never started")
        self.assertTrue(process_exists(foreign.pid),
                        "cleanup stopped a process this suite never started")

    def test_the_ledger_stops_a_recorded_process_and_the_children_it_left(self):
        script = os.path.join(self.workdir, "with-child.sh")
        with open(script, "w") as handle:
            handle.write("#!/bin/sh\nsleep 30 &\nsleep 30\n")
        os.chmod(script, 0o755)
        parent = subprocess.Popen([script], stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        LEDGER.note(parent.pid)
        deadline = time.monotonic() + 10.0
        children = []
        while time.monotonic() < deadline and not children:
            children = direct_children(parent.pid)
            time.sleep(0.05)
        self.assertTrue(children, "the stand-in left no child to check")
        self.assertEqual(LEDGER.reap(), [], "a recorded process survived cleanup")
        self.assertFalse(process_exists(parent.pid), "the stand-in survived")
        for child in children:
            self.assertFalse(process_exists(child), "a child survived cleanup")

    def test_a_ledger_only_ever_reports_the_processes_written_into_it(self):
        started = subprocess.Popen(["sleep", "30"], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        ledger = Ledger()
        ledger.note(started.pid)
        self.assertEqual(ledger.alive(), [started.pid])
        self.assertEqual(ledger.reap(), [])
        self.assertFalse(process_exists(started.pid))

    def test_a_process_that_already_ended_is_never_reported_as_surviving(self):
        ended = subprocess.Popen(["true"], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        ended.wait()
        ledger = Ledger()
        ledger.note(ended.pid)
        self.assertFalse(process_exists(ended.pid),
                         "an ended process was counted as running")
        self.assertEqual(ledger.alive(), [],
                         "a ledger reported a process that had already ended")
        self.assertEqual(ledger.reap(), [],
                         "a ledger reported an ended process as one it had to stop")

    def test_a_number_handed_to_another_process_is_never_signalled(self):
        """A pid the operating system reused must never be signalled.

        A pid alone cannot say whose it is: the operating system gives the same
        number to a process started after the one that held it.  The ledger
        therefore compares the creation time recorded for the number with the
        one the kernel reports now, and a number that no longer matches is no
        longer one this run may touch.
        """
        running = subprocess.Popen(["sleep", "30"], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        self.addCleanup(self.finish, running)
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and not process_exists(running.pid):
            time.sleep(0.02)
        ledger = Ledger()
        ledger.note(running.pid)
        # The kernel hands the same number to a later process: the entry keeps
        # its recorded creation time, and that is what no longer matches.
        ledger.entries = [(pid, "1") for pid, _ in ledger.entries]
        self.assertEqual(ledger.reap(), [],
                         "a ledger signalled a number it no longer owned")
        self.assertTrue(process_exists(running.pid),
                        "a ledger stopped a process it never started")
        self.assertEqual(ledger.pids, [],
                         "a number that stopped being ours stayed in the ledger")



    def test_a_process_whose_owner_cannot_be_proved_is_reported_not_signalled(self):
        """A pid whose owner cannot be proved is reported, never signalled.

        A creation time the kernel would not give makes ownership unprovable.
        Signalling such a number could reach a process this run never started,
        so cleanup leaves it alone and names it instead of pretending it stopped
        what it could not identify.
        """
        running = subprocess.Popen(["sleep", "30"], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        self.addCleanup(self.finish, running)
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and not process_exists(running.pid):
            time.sleep(0.02)
        ledger = Ledger()
        ledger.entries.append((running.pid, None))
        self.assertEqual(ledger.reap(), [],
                         "a ledger signalled a process it could not identify")
        self.assertTrue(process_exists(running.pid),
                        "cleanup stopped a process whose owner it could not prove")
        self.assertEqual(ledger.unidentified(), [running.pid],
                         "a survivor this run could not identify went unreported")


    def test_a_child_is_claimed_under_its_own_identity_before_it_is_stopped(self):
        """A child is recorded under its own identity before it is stopped.

        Walking the process table names a pid but says nothing about who owns
        it.  A cleanup pass that stops a child only because it hangs below one
        of our processes trusts the process table to still be telling the truth
        a moment later, so the child is recorded under the creation time the
        kernel gave it and guarded by the same ownership test as its parent.
        """
        parent = subprocess.Popen(["/bin/sh", "-c", "sleep 30 & sleep 30"],
                                  stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)
        self.addCleanup(self.finish, parent)
        children = []
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and not children:
            children = descendants(parent.pid)
            if not children:
                time.sleep(0.02)
        self.assertTrue(children, "the stand-in started no child to claim")
        ledger = Ledger()
        ledger.note(parent.pid)
        self.assertEqual(ledger.reap(), [],
                         "a cleanup pass left a recorded process running")
        self.assertFalse(process_exists(parent.pid),
                         "the process a test recorded survived its own cleanup")
        for child in children:
            self.assertFalse(process_exists(child),
                             "a child survived a cleanup that reached its parent")


class DatabaseHomeChecks(WindowCase):
    """Started from an unrelated directory, the front-end must still find chips.

    minipro reads infoic.xml and logicic.xml from its working directory or from
    MINIPRO_HOME.  Started from an unrelated directory, the front-end listed
    nothing at all and still reported the listing as finished.
    """

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="home-", dir=WORKSPACE)
        self.addCleanup(shutil.rmtree, self.home, True)
        self.saved = dict(os.environ)
        self.addCleanup(self.restore_environment)
        os.environ.pop("MINIPRO_HOME", None)
        super().setUp()

    def restore_environment(self):
        """Give back every engine variable one test had changed."""
        for name in ("MINIPRO_BIN", "MINIPRO_HOME"):
            os.environ.pop(name, None)
            if name in self.saved:
                os.environ[name] = self.saved[name]

    def engine_over_databases(self, script=STUB):
        """Copy one stand-in engine and its databases into one private directory."""
        binary = os.path.join(self.home, "minipro")
        shutil.copy(script, binary)
        for name in mg.DATABASE_FILES:
            with open(os.path.join(self.home, name), "w") as handle:
                handle.write("<database/>\n")
        return binary

    def reporting_stub(self):
        """Return a stand-in engine that reports the MINIPRO_HOME it received."""
        script = os.path.join(self.home, "report-home.sh")
        with open(script, "w") as handle:
            handle.write("#!/bin/sh\n"
                         "printf 'home=%s\\n' \"${MINIPRO_HOME:-none}\" >&2\n")
        os.chmod(script, 0o755)
        return script

    def test_the_directory_beside_the_engine_is_read_as_its_database_home(self):
        binary = self.engine_over_databases()
        self.assertEqual(mg.database_home(binary), os.path.abspath(self.home))

    def test_an_engine_without_databases_beside_it_is_left_to_its_own_cwd(self):
        self.assertIsNone(mg.database_home(STUB))

    def test_a_partial_database_pair_is_not_treated_as_a_database_home(self):
        binary = os.path.join(self.home, "minipro")
        shutil.copy(STUB, binary)
        with open(os.path.join(self.home, mg.DATABASE_FILES[0]), "w") as handle:
            handle.write("<database/>\n")
        self.assertIsNone(mg.database_home(binary))

    def test_a_home_the_operator_named_is_never_replaced_by_another(self):
        binary = self.engine_over_databases()
        os.environ["MINIPRO_HOME"] = self.home
        self.assertIsNone(mg.database_home(binary))

    def test_a_window_over_such_an_engine_advertises_the_home_it_found(self):
        window = self.make_window(self.engine_over_databases())
        self.assertEqual(window.database_home, os.path.abspath(self.home))
        self.assertIn("Bazele de date sunt citite din", window_journal(window))

    def test_the_database_home_is_handed_to_the_engine_as_MINIPRO_HOME(self):
        window = self.make_window(self.engine_over_databases(self.reporting_stub()))
        self.assertIn("home=%s" % os.path.abspath(self.home),
                      window_journal(window))


@unittest.skipUnless(ENGINE_REQUESTED, "MINIPRO_TEST_ENGINE=1 asks for these")
class EngineDatabaseChecks(unittest.TestCase):
    """The databases the front-end looks for are the ones the engine reads."""

    def test_the_shipped_databases_are_the_ones_the_front_end_names(self):
        for name in mg.DATABASE_FILES:
            self.assertTrue(os.path.isfile(os.path.join(ROOT, name)),
                            "%s is not beside the engine" % name)

    def test_the_engine_is_read_as_having_its_own_database_home(self):
        engine = os.path.join(ROOT, "minipro")
        self.assertTrue(os.access(engine, os.X_OK), "no engine built at " + engine)
        self.assertEqual(mg.database_home(engine), os.path.abspath(ROOT))


class LanguageChecks(WindowCase):
    """The experimental warning and the language the window speaks.

    Both are promises the window makes about itself: it never hides that it is
    experimental, and it answers in the language its operator chose.  A check
    that only reads the table would miss a control that was never registered,
    so these checks read the built window.
    """

    binary = SLOW_STUB

    def tearDown(self):
        # The suite drives the Romanian window, so a check that switched away
        # from it hands it back before the next one builds its window.
        mg.set_language("ro")
        mg.store_language("ro")
        super().tearDown()

    def test_the_window_shows_its_experimental_warning_from_the_start(self):
        banner = self.window.experimental_banner
        self.assertEqual(banner.objectName(), "experimentalBanner")
        self.assertEqual(banner.text(), mg.text_for("banner.experimental", "ro"))
        # The warning is part of the first thing the window lays out, so it is
        # above the write and erase buttons instead of somewhere to scroll to.
        self.assertIs(self.window.main_layout.itemAt(0).widget(), banner)
        self.assertTrue(banner.isEnabled())
        self.assertFalse(banner.isHidden())

    def test_the_banner_survives_a_language_switch(self):
        self.assertTrue(self.window.choose_language("en"))
        self.assertEqual(self.window.experimental_banner.text(),
                         mg.text_for("banner.experimental", "en"))
        self.assertTrue(self.window.experimental_banner.isEnabled())

    def test_the_banner_stays_readable_while_an_operation_runs(self):
        self.choose_chip()
        self.start(self.window.do_read)
        self.assertTrue(self.window.runner.running(), "the call already ended")
        self.assertFalse(self.window.read_button.isEnabled(),
                         "the controls were not locked while the call ran")
        self.assertTrue(self.window.experimental_banner.isEnabled(),
                        "the warning went away for the one moment it matters")
        self.assertFalse(self.window.experimental_banner.isHidden())
        self.assertEqual(self.window.experimental_banner.text(),
                         mg.text_for("banner.experimental", "ro"))
        self.settle(self.window)

    def test_switching_language_retitles_the_window_and_its_controls(self):
        before = len(self.window.menuBar().actions())
        self.assertTrue(self.window.choose_language("en"))
        self.assertEqual(self.window.windowTitle(),
                         mg.text_for("window.title", "en"))
        self.assertEqual(self.window.read_button.text(),
                         mg.text_for("action.read", "en"))
        self.assertEqual(self.window.advanced.title(),
                         mg.text_for("box.advanced", "en"))
        self.assertEqual(self.window.operations_menu.menuAction().text(),
                         mg.text_for("menu.operations", "en"))
        # Rebuilding the menus must replace them, never stack a second set.
        self.assertEqual(len(self.window.menuBar().actions()), before)
        # Asking for the language it already speaks changes nothing.
        self.assertFalse(self.window.choose_language("en"))

    def test_switching_language_keeps_what_the_operator_had_chosen(self):
        pick(self.window.icsp, "-i")
        pick(self.window.format, "ihex")
        self.window.vcc.setCurrentText("3.3")
        self.assertTrue(self.window.choose_language("en"))
        self.assertEqual(self.window.icsp.currentData(), "-i")
        self.assertEqual(self.window.icsp.currentText(),
                         mg.text_for("icsp.with_vcc", "en"))
        self.assertEqual(self.window.format.currentData(), "ihex")
        # A value the engine reported is a value, not a label: it is kept.
        self.assertEqual(self.window.vcc.currentText(), "3.3")

    def test_the_language_choice_reaches_the_next_window(self):
        self.assertTrue(self.window.choose_language("en"))
        other = self.make_window(self.binary)
        self.assertEqual(mg.language(), "en")
        self.assertEqual(other.read_button.text(),
                         mg.text_for("action.read", "en"))
        self.assertEqual(other.experimental_banner.text(),
                         mg.text_for("banner.experimental", "en"))

    def test_switching_language_keeps_the_answers_the_engine_gave(self):
        # The header shows what the engine reported.  Retranslating must not
        # turn an answer back into the waiting text it replaced.
        self.window.apply_engine_info(
            "minipro version 1.0.8 (build 7)\n"
            "Git commit:\t0123456789abcdef0123456789abcdef0123456789\n")
        self.window.apply_programmer("tl866a: TL866A is connected")
        self.assertTrue(self.window.choose_language("en"))
        self.assertIn("0123456789", self.window.engine_label.text())
        self.assertEqual(self.window.programmer_label.text(), "tl866a")
        self.window.apply_programmer("[No programmer found]")
        self.assertTrue(self.window.choose_language("ro"))
        self.assertEqual(self.window.programmer_label.text(),
                         mg.text_for("header.no_programmer", "ro"))

    def test_every_control_the_window_records_can_be_retitled(self):
        # The window keeps a list of the controls whose words it owns.  Each
        # entry must name a key the table can answer for and a kind the
        # retranslation knows how to write, or switching language leaves that
        # one control speaking the old language.
        self.assertTrue(self.window._texts, "the window recorded no text at all")
        for widget, kind, key in self.window._texts:
            self.assertIn(key, mg.TEXTS,
                          "%s was recorded with an unknown key" % key)
            self.assertIsInstance(widget, QtWidgets.QWidget)
            self.assertIn(kind, ("text", "title", "tip", "placeholder"))
        for combo, items in self.window._combos:
            for _value, label in items():
                self.assertTrue(label.strip(),
                                "one combo entry would show no text")

    def test_the_view_menu_offers_each_language_once_and_marks_the_active(self):
        actions = self.window.language_menu.actions()
        # Each entry is written in the language it names, so an operator who
        # cannot read the language the window is stuck in can still find their
        # own by name.
        self.assertEqual([name for _code, name in mg.LANGUAGE_CHOICES],
                         [action.text() for action in actions])
        self.assertEqual(len(actions), len(mg.LANGUAGE_CHOICES))
        checked = [action for action in actions if action.isChecked()]
        self.assertEqual(len(checked), 1, "the active language is not marked")
        self.assertTrue(self.window.choose_language("en"))
        checked = [action for action in self.window.language_menu.actions()
                   if action.isChecked()]
        self.assertEqual(len(checked), 1,
                         "switching left more than one language marked")


class TranslationTableChecks(unittest.TestCase):
    """The keyed table itself: complete, two-sided, and free of diacritics."""

    def body(self):
        """Return the front-end source with the keyed table cut out of it."""
        with open(GUI_PATH, encoding="utf-8") as handle:
            source = handle.read()
        start = source.index("TEXTS = {")
        end = source.index("\n}\n", start)
        return source[:start] + source[end:], source

    def test_every_key_the_front_end_asks_for_is_in_the_table(self):
        with open(GUI_PATH, encoding="utf-8") as handle:
            source = handle.read()
        # tr() is how a key is read, wherever it is read from: a builder that
        # was handed a key the table never stored would have raised while the
        # window was built, which every window check here already proves.
        asked = set(re.findall(r'tr\("([a-z0-9_.]+)"\)', source))
        self.assertEqual(sorted(asked - set(mg.TEXTS)), [],
                         "keys asked for but never stored")

    def test_every_row_carries_both_languages(self):
        for key, row in mg.TEXTS.items():
            self.assertEqual(len(row), 2, "%s does not name two languages" % key)
            for half in row:
                self.assertIsInstance(half, str, "%s holds a non-text" % key)
                self.assertTrue(half.strip(), "%s holds an empty half" % key)

    def test_the_romanian_half_never_relies_on_diacritics(self):
        # A terminal or a font without them turns these letters into boxes,
        # and the warning has to stay readable on the machine it warns.
        letters = "ăâîșțĂÂÎȘȚ"
        offenders = [key for key, row in mg.TEXTS.items()
                     if any(letter in row[1] for letter in letters)]
        self.assertEqual(offenders, [], "keys whose Romanian half has diacritics")
        with open(GUI_PATH, encoding="utf-8") as handle:
            self.assertNotRegex(handle.read(), "[%s]" % letters)

    def test_no_row_of_the_table_is_left_unused(self):
        # A key the front-end never asks for is a label that was dropped: the
        # control it belonged to went away, or the call that showed it was
        # rewritten and forgot to ask.  Either way the row hides that.
        with open(GUI_PATH, encoding="utf-8") as handle:
            source = handle.read()
        start = source.index("TEXTS = {")
        end = source.index("\n}\n", start)
        table, body = source[start:end], source[:start] + source[end:]
        keys = re.findall(r'^    "([a-z0-9_.]+)":', table, re.M)
        self.assertTrue(keys, "the table holds no rows at all")
        unused = [key for key in keys
                  if not re.search(r'["(]%s[")]' % re.escape(key), body)]
        self.assertEqual(unused, [], "rows the front-end never asks for")

    def test_no_control_is_left_with_a_hard_coded_label(self):
        # Every label the window shows comes from the table, so a call that
        # hands a literal string to a text setter is a label that will never
        # switch language.
        body, _source = self.body()
        offenders = re.findall(r'(?:setText|setTitle|setWindowTitle|setToolTip'
                               r'|setPlaceholderText)\("([^"]+)"', body)
        self.assertEqual(offenders, [], "labels set outside the table")


def main():
    """Run every sealed check, and the engine checks only when they are asked for."""
    suite = unittest.TestSuite()
    for name in ("CommandLineChecks", "FileSafetyChecks", "ChipContextChecks",
                  "ChipContextDatabaseAttributionChecks", "ResultChecks",
                  "ProcessChecks", "CloseChecks",
                  "StubbornCloseChecks", "ParsingChecks",
                  "ChannelDecodingChecks", "EngineLookupChecks",
                  "MissingEngineChecks", "DatabaseHomeChecks",
                  "ProcessIsolationChecks", "EngineDatabaseChecks",
                  "LanguageChecks", "TranslationTableChecks"):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromName(
            name, sys.modules[__name__]))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
