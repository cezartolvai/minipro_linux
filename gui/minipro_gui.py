#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PyQt5 front-end for the upstream minipro programmer command line tool.

The engine is the minipro binary built from the upstream checkout next to this
directory; this program only builds command lines and presents progress, chip
information and logs.  Command line rules mirror src/main.c of that engine:

  * -q selects a database and main.c accepts it only together with -l, -L or
    -d, so the GUI uses it for listing, searching and chip information only,
    never for a programming operation.
  * -x (skip ID) is rejected by main.c for write, erase and ID-only actions,
    so the GUI never sends it there.
  * -m <file> is the verify action and -E is the erase action, while -v and -e
    only skip the verify and erase stages of another action; -V only reports
    engine information.
  * Voltage and SPI clock overrides are optional: without them minipro uses the
    values stored in the database of the selected chip.
"""

import argparse
import codecs
import os
import re
import shlex
import sys
import time
from shutil import which

from PyQt5 import QtCore, QtWidgets


# ------------------------------------------------------------- CLI reference
# Database selectable with -q (main.c, case 'q').
DB_FILTERS = (
    ("tl866a", "TL866A/CS"),
    ("tl866ii", "TL866II+"),
    ("t48", "T48"),
    ("t56", "T56"),
    ("t76", "T76"),
)

# Memory regions accepted by -c (main.c, case 'c').
MEMORY_VALUES = (
    (None, "Implicit (alege minipro)"),
    ("code", "code"),
    ("data", "data"),
    ("config", "config"),
    ("user", "user"),
    ("calibration", "calibration"),
)

# Configuration filters: long options only, no short letter (main.c).
CONFIG_SECTIONS = (
    (None, "Toata regiunea config"),
    ("--fuses", "doar fuse-urile"),
    ("--uid", "doar user id"),
    ("--lock", "doar lock bits"),
)

# File formats accepted by -f (main.c, case 'f').
FORMAT_VALUES = (
    (None, "Auto (binar)"),
    ("ihex", "Intel HEX"),
    ("srec", "S-Record"),
)

# -o ovc_level=N (main.c, parse_options): 0 is the firmware default and 1..9
# are the 120/150/180/200/220/250/300/350/400 mA trip levels (minipro.h).
OVC_MILLIAMPERES = (120, 150, 180, 200, 220, 250, 300, 350, 400)
OVC_LEVELS = ((0, "Implicit (din firmware)"),) + tuple(
    (level, "%d mA" % milli) for milli, level in zip(OVC_MILLIAMPERES,
                                                    range(1, 10)))

# Shown by every override control when minipro should pick the value itself.
IMPLICIT = "Implicit (valoarea din cip)"

# How long a cancelled engine call gets to stop before it is killed, and how
# long a closing window waits for that to happen.
CANCEL_GRACE_MS = 3000
CLOSE_GRACE_MS = 4000
CLOSE_KILL_GRACE_MS = 2000

# minipro writes progress as "\r\e[K<label><percent>%" records on stderr.
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
PERCENT_RE = re.compile(r"(\d{1,3})\s*%")
VALUE_LINE_RE = re.compile(r"^[0-9][0-9., ]*$")

# A record carrying one of these words is a message, not a progress update.
RESULT_MARKERS = ("OK", "ms", "Sec", "Success", "Failure", "ERROR", "error",
                  "Warning", "Found", "not found", "NOT BLANK", "BLANK OK",
                  "device(s) found", "Aborted")

# Action flags this front-end uses: -m verifies, -E erases, -D reads the ID.
ACTION_FLAGS = {
    "read": "-r",
    "write": "-w",
    "verify": "-m",
    "erase": "-E",
    "blank": "-b",
    "id": "-D",
    "pin": "-z",
}

# Text shown for each action in dialogs and in the journal.
ACTION_LABELS = {
    "read": "citire",
    "write": "scriere",
    "verify": "verificare",
    "erase": "stergere",
    "blank": "verificare gol",
    "id": "citire ID",
    "pin": "verificare pini",
}

# Status and journal text for each action: the verb is stated only once.
ACTION_TITLES = {
    "read": "Citire",
    "write": "Scriere",
    "verify": "Verificare",
    "erase": "Stergere",
    "blank": "Verificare gol",
    "id": "Citire ID",
    "pin": "Verificare pini",
}

# main.c rejects -x for these actions, so the GUI never sends it there.
SKIP_ID_ACTIONS = ("read", "verify", "blank", "pin")

# ICSP modes: -i supplies VCC, -I does not (main.c, case 'i' and 'I').
ICSP_VALUES = (
    (None, "Mod standard (fara ICSP)"),
    ("-i", "ICSP cu VCC (-i)"),
    ("-I", "ICSP fara VCC (-I)"),
)

# File size handling: -s ignores the difference, while -S makes main.c treat a
# size difference as size_nowarn plus size_error, so it does not merely hide a
# warning: the operation continues with a file of a different size.
SIZE_MODES = (
    (None, "Eroare la diferenta de marime"),
    ("-s", "Ignora diferenta de marime (-s)"),
    ("-S", "Continua cu alta marime, fara avertisment (-S)"),
)


# --------------------------------------------------------------- text parsing
def strip_ansi(text):
    """Remove ANSI escape sequences from one minipro status record."""
    return ANSI_RE.sub("", text)


def parse_version(text):
    """Return the engine version reported by -V."""
    match = re.search(r"minipro version (\S+)", text)
    return match.group(1) if match else ""


def parse_commit(text):
    """Return the short git commit reported by -V."""
    match = re.search(r"Git commit:\s+(\S+)", text)
    return match.group(1)[:10] if match else ""


def parse_chip_counts(text):
    """Map every programmer to the number of chips -V reports for it."""
    names = {"TL866A/CS": "tl866a", "TL866II+": "tl866ii", "T48": "t48",
             "T56": "t56", "T76": "t76"}
    pattern = r"(TL866A/CS|TL866II\+|T48|T56|T76):\s+(\d+) devices"
    counts = {}
    for label, count in re.findall(pattern, text):
        counts[names[label]] = int(count)
    return counts


def parse_programmer(text):
    """Return the programmer -k reports, or None when none is connected."""
    match = re.search(r"\b(tl866a|tl866ii|t48|t56|t76):", text)
    return match.group(1) if match else None



def _split_values(fragment):
    """Split one comma separated parameter table fragment."""
    names = []
    for piece in fragment.split(","):
        name = piece.strip()
        if name:
            names.append(name)
    return names


def parse_available_values(text, header):
    """Return the values minipro lists under one chip info heading."""
    lines = strip_ansi(text).splitlines()
    for position, line in enumerate(lines):
        if not line.startswith(header):
            continue
        values = _split_values(line[len(header):])
        for following in lines[position + 1:]:
            if not VALUE_LINE_RE.match(following):
                break
            values.extend(_split_values(following))
        return tuple(values)
    return ()


def display_command(binary, args):
    """Return the command line exactly as this front-end would run it."""
    return " ".join(shlex.quote(part) for part in [binary] + list(args))


class BinaryNotFound(Exception):
    """The minipro engine could not be located."""


def find_binary(explicit=None):
    """Locate the engine: argument, environment, build tree, then PATH."""
    if explicit:
        if not os.path.isfile(explicit) or not os.access(explicit, os.X_OK):
            raise BinaryNotFound("Nu am gasit minipro la: %s" % explicit)
        return os.path.abspath(explicit)
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [os.environ.get("MINIPRO_BIN"), os.path.join(here, "minipro"),
                  os.path.join(os.path.dirname(here), "minipro")]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and \
                os.access(candidate, os.X_OK):
            return os.path.abspath(candidate)
    found = which("minipro")
    if found:
        return os.path.abspath(found)
    raise BinaryNotFound("Nu am gasit executabilul minipro. Constructeaza-l "
                         "sau arata-l cu --binary <cale>.")



# Databases minipro reads at run time (src/database.c, get_database_file).
DATABASE_FILES = ("infoic.xml", "logicic.xml")


def database_home(binary):
    """Return the directory holding the databases of one engine, or None.

    minipro looks for infoic.xml and logicic.xml first in its working
    directory and then in MINIPRO_HOME, so a front-end started from an
    unrelated directory lists no chip at all.  When the databases sit next to
    the engine, that directory is handed to the engine as MINIPRO_HOME.
    """
    if os.environ.get("MINIPRO_HOME"):
        return None
    directory = os.path.dirname(os.path.abspath(binary))
    if all(os.path.isfile(os.path.join(directory, name))
           for name in DATABASE_FILES):
        return directory
    return None


class Runner(QtCore.QObject):
    """Run one minipro call without blocking the user interface.

    minipro writes progress to stderr and listings to stdout, so the two
    channels are kept apart: stderr drives the log and the progress bar, while
    stdout is kept for the device list.  Progress records arrive as
    "\\r\\e[K<label><percent>%" fragments, so records are split on carriage
    returns and on newlines before the ANSI escapes are removed.

    Two details matter over a long call.  A UTF-8 character can be split across
    two reads, so the bytes are decoded through one incremental decoder instead
    of one chunk at a time.  And the kept text is capped: a call that writes
    megabytes of progress reports the cap instead of growing the front-end
    without end.
    """

    progress = QtCore.pyqtSignal(int)
    log_line = QtCore.pyqtSignal(str)
    done = QtCore.pyqtSignal(int, str)

    # Characters kept per channel for one finished call.
    CAPTURE_LIMIT = 1 << 20

    def __init__(self):
        super().__init__()
        self.stdout_text = ""
        self.stderr_text = ""
        self.captured_truncated = False
        self.captured_limit = self.CAPTURE_LIMIT
        self._partial = ""
        # stdout and stderr are two independent byte streams, so each gets its
        # own incremental decoder: a character split across two reads must stay
        # in the channel it came from instead of leaking into the other one.
        self._stdout_decoder = self._new_decoder()
        self._stderr_decoder = self._new_decoder()
        self.reported = True
        self.cancelled = False
        self._generation = 0
        self._proc = None
        self._kill = QtCore.QTimer(self)
        self._kill.setSingleShot(True)
        self._kill.timeout.connect(self._force_kill)

    @staticmethod
    def _new_decoder():
        """Build one incremental UTF-8 decoder for one channel of one call."""
        return codecs.getincrementaldecoder("utf-8")("replace")

    # ------------------------------------------------------------- lifecycle
    def start(self, binary, args, label, home=None):
        """Start one minipro call and report its label to the progress bar."""
        if self.running():
            return False
        self.stdout_text = ""
        self.stderr_text = ""
        self.captured_truncated = False
        self._partial = ""
        self._stdout_decoder = self._new_decoder()
        self._stderr_decoder = self._new_decoder()
        self.cancelled = False
        self.reported = False
        self.progress.emit(-1)
        self.log_line.emit(display_command(binary, args))
        process = QtCore.QProcess(self)
        process.setProgram(binary)
        process.setArguments(list(args))
        if home:
            # The working directory is left alone so the file paths the user
            # typed keep resolving against it; the databases are advertised
            # through the variable minipro itself reads.
            environment = QtCore.QProcessEnvironment.systemEnvironment()
            environment.insert("MINIPRO_HOME", home)
            process.setProcessEnvironment(environment)
        # minipro asks on the terminal when it cannot find a programmer; a
        # closed input makes it fail here instead of waiting inside the GUI.
        process.setStandardInputFile(os.devnull)
        # Every callback names the call it belongs to, so a late record from a
        # process this runner already dropped cannot touch the current one.
        self._generation += 1
        generation = self._generation
        process.readyReadStandardOutput.connect(
            lambda: self._on_stdout(generation))
        process.readyReadStandardError.connect(
            lambda: self._on_stderr(generation))
        process.finished.connect(lambda code, status: self._on_finished(
            generation, code, status))
        process.errorOccurred.connect(
            lambda error: self._on_error(generation, error=error))
        self._proc = process
        process.start()
        return True

    def running(self):
        """Report whether a minipro process is currently active."""
        return (self._proc is not None and not self.reported and
                self._proc.state() != QtCore.QProcess.NotRunning)

    def process_id(self):
        """Return the system id of the running engine call, or None."""
        if self._proc is None or \
                self._proc.state() == QtCore.QProcess.NotRunning:
            return None
        return int(self._proc.processId())

    def cancel(self):
        """Ask minipro to stop, arming the escalation only once."""
        if not self.running():
            return False
        if not self.cancelled:
            self.log_line.emit("Anulare: se opreste minipro...")
        self.cancelled = True
        self._proc.terminate()
        if not self._kill.isActive():
            # A second click must not keep pushing the kill deadline forward.
            self._kill.start(CANCEL_GRACE_MS)
        return True

    def force_kill(self):
        """Kill a minipro process that ignored the terminate request."""
        if self._proc is not None and \
                self._proc.state() != QtCore.QProcess.NotRunning:
            self._proc.kill()

    def _force_kill(self):
        """Escalate a cancel whose grace period ran out."""
        self.force_kill()

    def _release(self):
        """Drop a finished process so repeated calls do not pile up children."""
        process, self._proc = self._proc, None
        if process is None:
            return
        for channel in (process.readyReadStandardOutput,
                        process.readyReadStandardError, process.finished,
                        process.errorOccurred):
            try:
                channel.disconnect()
            except (TypeError, RuntimeError):
                pass
        process.deleteLater()

    # ------------------------------------------------------------ callbacks
    def _current(self, generation):
        """Report whether one callback still belongs to the live call."""
        return generation == self._generation and self._proc is not None

    def _decode_stdout(self, chunk, final=False):
        """Decode stdout bytes through the decoder of the stdout channel."""
        return self._stdout_decoder.decode(bytes(chunk), final)

    def _decode_stderr(self, chunk, final=False):
        """Decode stderr bytes through the decoder of the stderr channel."""
        return self._stderr_decoder.decode(bytes(chunk), final)

    def _read_stdout(self):
        """Return the stdout bytes of the live process."""
        return b"" if self._proc is None else bytes(
            self._proc.readAllStandardOutput())

    def _read_stderr(self):
        """Return the stderr bytes of the live process."""
        return b"" if self._proc is None else bytes(
            self._proc.readAllStandardError())

    def _on_stdout(self, generation):
        """Collect listings, which minipro writes to stdout."""
        if not self._current(generation):
            return
        self.stdout_text = self._capture(self.stdout_text, self._decode_stdout(
            self._read_stdout()))

    def _on_stderr(self, generation):
        """Feed progress and diagnostics to the log and the progress bar."""
        if not self._current(generation):
            return
        self._feed(self._read_stderr())

    def _on_finished(self, generation, exit_code, exit_status):
        """Turn one engine exit into the exit code shown to the user."""
        if not self._current(generation):
            return
        if exit_status == QtCore.QProcess.NormalExit:
            self._finish(int(exit_code))
        else:
            self._finish(1)

    def _on_error(self, generation, error):
        """Report a process that never started; other errors end via finished."""
        if not self._current(generation):
            return
        if error == QtCore.QProcess.FailedToStart:
            self.log_line.emit("Nu am putut porni procesul minipro.")
            self._finish(127)
        elif error == QtCore.QProcess.Timedout:
            self.log_line.emit("Procesul minipro nu a raspuns la timp.")

    def _finish(self, exit_code):
        """Report completion once, after the last output was drained."""
        if self.reported:
            return
        self.reported = True
        self._generation += 1
        self._kill.stop()
        # Each channel is drained and finalized on its own: the trailing bytes
        # of one stream belong to that channel and to no other channel.
        self.stdout_text = self._capture(self.stdout_text, self._decode_stdout(
            self._read_stdout(), final=True))
        self._feed(self._read_stderr())
        self._flush_pending()
        if self.captured_truncated:
            self.log_line.emit("Jurnalul operatiei a fost trunchiat la %d "
                               "caractere." % self.captured_limit)
        self.progress.emit(100 if exit_code == 0 else 0)
        self.done.emit(int(exit_code), self.stdout_text)
        self._release()

    # -------------------------------------------------------------- internals
    def _capture(self, current, text):
        """Append captured text, never past the cap this runner keeps."""
        room = max(0, self.captured_limit - len(current))
        if len(text) > room:
            self.captured_truncated = True
            return current + text[:room]
        return current + text

    def _feed(self, chunk):
        """Split one stderr chunk into the records minipro writes."""
        if not chunk:
            return
        text = self._partial + self._decode_stderr(chunk, False)
        records = re.split(r"[\r\n]", text)
        self._partial = records.pop()
        for record in records:
            self.stderr_text = self._capture(self.stderr_text, record + "\n")
            self._report(strip_ansi(record))

    def _flush_pending(self):
        """Report a final record that minipro did not terminate itself."""
        self._partial += self._decode_stderr(b"", final=True)
        if self._partial:
            tail = strip_ansi(self._partial)
            self.stderr_text = self._capture(self.stderr_text,
                                             self._partial + "\n")
            self._partial = ""
            self._report(tail)

    def _report(self, text):
        """Send one cleaned record to the log and update the progress bar."""
        if not text.strip():
            return
        percent = PERCENT_RE.search(text)
        if percent and not any(marker in text for marker in RESULT_MARKERS):
            self.progress.emit(min(100, int(percent.group(1))))
            return
        self.log_line.emit(text)


class MiniproGui(QtWidgets.QMainWindow):
    """Interactive front-end that drives the minipro engine."""

    def __init__(self, binary):
        super().__init__()
        self.binary = binary
        self.setWindowTitle("MiniPro GUI")
        self.resize(1000, 760)
        self.runner = Runner()
        self.engine_version = ""
        self.engine_commit = ""
        self.engine_counts = {}
        self.expect = None
        self.database_home = database_home(binary)

        central = QtWidgets.QWidget()
        self.main_layout = QtWidgets.QVBoxLayout(central)
        self.setCentralWidget(central)

        self._build_header()
        self._build_device_box()
        self._build_file_box()
        self._build_operations_box()
        self._build_advanced_box()
        self._build_log_area()
        self._build_status_row()
        self._build_menus()
        self._wire_runner()
        if self.database_home:
            self._log("Bazele de date sunt citite din %s."
                      % self.database_home)
        self.update_file_info()
        self.refresh_programmer()

    # ------------------------------------------------------------------ header
    def _build_header(self):
        box = QtWidgets.QGroupBox("Programator si motor")
        grid = QtWidgets.QGridLayout(box)
        grid.addWidget(QtWidgets.QLabel("Programator conectat:"), 0, 0)
        self.programmer_label = QtWidgets.QLabel("in verificare...")
        grid.addWidget(self.programmer_label, 0, 1)
        grid.addWidget(QtWidgets.QLabel("Motor minipro:"), 1, 0)
        self.engine_label = QtWidgets.QLabel("in verificare...")
        grid.addWidget(self.engine_label, 1, 1)
        self.programmer_button = QtWidgets.QPushButton("Reconecteaza")
        self.programmer_button.clicked.connect(self.refresh_programmer)
        grid.addWidget(self.programmer_button, 0, 2)
        self.engine_button = QtWidgets.QPushButton("Detalii motor")
        self.engine_button.clicked.connect(self.show_engine_info)
        grid.addWidget(self.engine_button, 1, 2)
        grid.setColumnStretch(1, 1)
        self.main_layout.addWidget(box)


    # ------------------------------------------------------------ device group
    def _build_device_box(self):
        box = QtWidgets.QGroupBox("Dispozitiv")
        grid = QtWidgets.QGridLayout(box)

        grid.addWidget(QtWidgets.QLabel("Baza de date (listare / info):"),
                       0, 0)
        self.db_filter = QtWidgets.QComboBox()
        for value, label in DB_FILTERS:
            self.db_filter.addItem(label, value)
        self.db_filter.setToolTip(
            "Alege baza de date interogata de -q la listare, cautare si\n"
            "informatii cip (-l, -L, -d). Nu este trimis niciodata la o\n"
            "operatie de programare: acolo minipro foloseste programatorul\n"
            "gasit pe USB.")
        grid.addWidget(self.db_filter, 0, 1)
        self.db_count_label = QtWidgets.QLabel("")
        grid.addWidget(self.db_count_label, 0, 2)

        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("ex. W25Q16, AT28C256, ATmega328P")
        self.search.returnPressed.connect(self.do_search)
        self.search_button = QtWidgets.QPushButton("Cauta cip")
        self.search_button.clicked.connect(self.do_search)
        grid.addWidget(self.search, 1, 0, 1, 2)
        grid.addWidget(self.search_button, 1, 2)

        grid.addWidget(QtWidgets.QLabel("Dispozitiv (-p):"), 2, 0)
        self.devices = QtWidgets.QComboBox()
        self.devices.setEditable(True)
        self.devices.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self.devices.setToolTip("Nume exact din baza de date, de obicei "
                                "NUME@CAPSULA")
        grid.addWidget(self.devices, 2, 1)
        self.info_button = QtWidgets.QPushButton("Informatii cip")
        self.info_button.clicked.connect(self.do_chip_info)
        grid.addWidget(self.info_button, 2, 2)

        self.autodetect_combo = QtWidgets.QComboBox()
        self.autodetect_combo.addItem("SPI 8 pini", "8")
        self.autodetect_combo.addItem("SPI 16 pini", "16")
        self.autodetect_button = QtWidgets.QPushButton("Autodetectare SPI")
        self.autodetect_button.clicked.connect(self.do_autodetect)
        self.list_button = QtWidgets.QPushButton("Listeaza cipurile")
        self.list_button.clicked.connect(self.do_list_devices)
        grid.addWidget(self.autodetect_combo, 3, 0)
        grid.addWidget(self.autodetect_button, 3, 1)
        grid.addWidget(self.list_button, 3, 2)

        # Values read out of one chip record belong to that chip alone, so
        # changing the chip or the database it came from invalidates them.
        self.chip_overrides_for = None
        self.chip_overrides_from = None
        # The database one chip record was actually queried from, recorded when
        # the query is built instead of when it finishes.  Until a query runs,
        # the values a window shows can only have come from the database it
        # already shows, so that is where the record is attributed.
        self.db_queried = self.db_filter.currentData()
        self.devices.currentTextChanged.connect(self._on_chip_context_changed)
        self.db_filter.currentIndexChanged.connect(
            self._on_chip_context_changed)

        grid.setColumnStretch(1, 1)
        self.main_layout.addWidget(box)


    # -------------------------------------------------------------- file group
    def _build_file_box(self):
        box = QtWidgets.QGroupBox("Fisier si memorie")
        grid = QtWidgets.QGridLayout(box)

        grid.addWidget(QtWidgets.QLabel("Fisier dump:"), 0, 0)
        self.file = QtWidgets.QLineEdit()
        self.file.setText(os.path.join(os.getcwd(), "dump.bin"))
        self.file.textChanged.connect(self.update_file_info)
        self.open_button = QtWidgets.QPushButton("Deschide...")
        self.open_button.clicked.connect(self.choose_input_file)
        self.save_button = QtWidgets.QPushButton("Salveaza ca...")
        self.save_button.clicked.connect(self.choose_output_file)
        grid.addWidget(self.file, 0, 1)
        grid.addWidget(self.open_button, 0, 2)
        grid.addWidget(self.save_button, 0, 3)

        grid.addWidget(QtWidgets.QLabel("Memorie (-c):"), 1, 0)
        self.memory = QtWidgets.QComboBox()
        for value, label in MEMORY_VALUES:
            self.memory.addItem(label, value)
        self.memory.setToolTip("-c: regiunea de memorie. Fara -c minipro alege\n"
                               "singur regiunea, iar comportamentul implicit\n"
                               "poate diferi intre citire si scriere.")
        grid.addWidget(self.memory, 1, 1)

        grid.addWidget(QtWidgets.QLabel("Regiune config:"), 1, 2)
        self.cfg_section = QtWidgets.QComboBox()
        for value, label in CONFIG_SECTIONS:
            self.cfg_section.addItem(label, value)
        self.cfg_section.setToolTip("--fuses / --uid / --lock: limiteaza\n"
                                    "operatia la fuse-uri, user id sau lock bits.\n"
                                    "Toate trei forțeaza -c config.")
        grid.addWidget(self.cfg_section, 1, 3)

        grid.addWidget(QtWidgets.QLabel("Format fisier (-f):"), 2, 0)
        self.format = QtWidgets.QComboBox()
        for value, label in FORMAT_VALUES:
            self.format.addItem(label, value)
        self.format.setToolTip("-f: format de fisier pentru -r, -w si -m.")
        grid.addWidget(self.format, 2, 1)

        self.file_info = QtWidgets.QLabel("")
        grid.addWidget(self.file_info, 2, 2, 1, 2)
        grid.setColumnStretch(1, 1)
        self.main_layout.addWidget(box)

    # ---------------------------------------------------------- operations box
    def _build_operations_box(self):
        box = QtWidgets.QGroupBox("Operatii")
        grid = QtWidgets.QGridLayout(box)

        self.read_button = QtWidgets.QPushButton("Citeste")
        self.read_button.clicked.connect(self.do_read)
        self.verify_button = QtWidgets.QPushButton("Verifica")
        self.verify_button.clicked.connect(self.do_verify)
        self.blank_button = QtWidgets.QPushButton("Verifica gol")
        self.blank_button.clicked.connect(self.do_blank)
        self.id_button = QtWidgets.QPushButton("Citeste ID")
        self.id_button.clicked.connect(self.do_id)
        self.write_button = QtWidgets.QPushButton("Scrie")
        self.write_button.clicked.connect(self.do_write)
        self.erase_button = QtWidgets.QPushButton("Sterge")
        self.erase_button.clicked.connect(self.do_erase)
        self.erase_button.setStyleSheet(
            "QPushButton { color: #a00000; font-weight: bold; }")
        self.pin_button = QtWidgets.QPushButton("Verifica pini")
        self.pin_button.clicked.connect(self.do_pin_check)

        grid.addWidget(self.read_button, 0, 0)
        grid.addWidget(self.verify_button, 0, 1)
        grid.addWidget(self.blank_button, 0, 2)
        grid.addWidget(self.id_button, 0, 3)
        grid.addWidget(self.write_button, 1, 0)
        grid.addWidget(self.erase_button, 1, 1)
        grid.addWidget(self.pin_button, 1, 2)
        self.main_layout.addWidget(box)

    # ----------------------------------------------------------- advanced box
    def _build_advanced_box(self):
        box = QtWidgets.QGroupBox("Optiuni avansate")
        box.setCheckable(True)
        box.setChecked(False)
        self.advanced = box
        grid = QtWidgets.QGridLayout(box)

        grid.addWidget(QtWidgets.QLabel("Mod ICSP:"), 0, 0)
        self.icsp = QtWidgets.QComboBox()
        for value, label in ICSP_VALUES:
            self.icsp.addItem(label, value)
        self.icsp.setToolTip("-i: ICSP cu VCC, -I: ICSP fara VCC. Fara ele "
                             "minipro programeaza prin socket.")
        grid.addWidget(self.icsp, 0, 1)

        grid.addWidget(QtWidgets.QLabel("VCC (override):"), 0, 2)
        self.vcc = self._param_combo("--vcc: tensiune de citire si verificare "
                                     "in volti.\nImplicit: valoarea din baza "
                                     "de date a cipului.")
        grid.addWidget(self.vcc, 0, 3)

        grid.addWidget(QtWidgets.QLabel("VPP (override):"), 1, 0)
        self.vpp = self._param_combo("--vpp: tensiune de programare in volti, "
                                     "pentru EPROM, EEPROM si MCU.")
        grid.addWidget(self.vpp, 1, 1)

        grid.addWidget(QtWidgets.QLabel("VDD (override):"), 1, 2)
        self.vdd = self._param_combo("--vdd: tensiune de scriere in volti.")
        grid.addWidget(self.vdd, 1, 3)

        grid.addWidget(QtWidgets.QLabel("SPI clock (MHz):"), 2, 0)
        self.spi_clock = self._param_combo(
            "--spi_clock: frecventa SPI in MHz. Valorile acceptate depind de "
            "cip: apasa\n\"Informatii cip\" ca lista de aici sa fie completata "
            "din baza de date.")
        grid.addWidget(self.spi_clock, 2, 1)

        grid.addWidget(QtWidgets.QLabel("Impuls programare (us):"), 2, 2)
        self.pulse = self._free_override("--pulse: durata impulsului de "
                                         "programare in microsecunde (1-65535).")
        grid.addWidget(self.pulse, 2, 3)

        grid.addWidget(QtWidgets.QLabel("Adresa I2C:"), 3, 0)
        self.address = self._free_override("--address: adresa I2C, zecimala "
                                        "sau 0x..., pentru cipuri I2C.")
        grid.addWidget(self.address, 3, 1)

        grid.addWidget(QtWidgets.QLabel("Nivel supracurent:"), 3, 2)
        self.ovc = QtWidgets.QComboBox()
        for level, label in OVC_LEVELS:
            self.ovc.addItem(label, level)
        self.ovc.setToolTip("-o ovc_level=N: limita de curent a "
                            "programatorului.\nImplicit: limita aleasa de "
                            "firmware.")
        grid.addWidget(self.ovc, 3, 3)

        grid.addWidget(QtWidgets.QLabel("Marime fisier:"), 4, 0)
        self.size_mode = QtWidgets.QComboBox()
        for value, label in SIZE_MODES:
            self.size_mode.addItem(label, value)
        self.size_mode.setToolTip("-s: ignora diferenta de marime dintre "
                                  "fisier si memorie.\n-S: main.c seteaza atat "
                                  "size_nowarn cat si size_error, deci o "
                                  "marime diferita\nnu este doar anuntata mai "
                                  "tarziu: operatia continua cu ea.")
        grid.addWidget(self.size_mode, 4, 1)

        self.skip_erase = QtWidgets.QCheckBox("Sari peste stergere (-e)")
        self.skip_erase.setToolTip("-e: nu sterge inainte de scriere.")
        self.skip_verify = QtWidgets.QCheckBox("Nu verifica dupa scriere")
        self.skip_verify.setToolTip("-v: sari peste verificarea de dupa "
                                    "scriere.")
        self.skip_blank = QtWidgets.QCheckBox("Sari peste blocurile goale")
        self.skip_blank.setToolTip("-B: sare peste blocurile care sunt goale.")
        self.unprotect = QtWidgets.QCheckBox("Deblocheaza scrierea")
        self.unprotect.setToolTip("-u: scoate protectia de scriere inainte de "
                                    "operatie.")
        grid.addWidget(self.skip_erase, 5, 0)
        grid.addWidget(self.skip_verify, 5, 1)
        grid.addWidget(self.skip_blank, 5, 2)
        grid.addWidget(self.unprotect, 5, 3)

        self.skip_id = QtWidgets.QCheckBox("Nu citi ID-ul")
        self.skip_id.setToolTip("-x: nu citi ID-ul inainte de operatie.\n"
                                "main.c refuza saltul la scriere, stergere si "
                                "citire de ID, deci acolo este ignorat.")
        self.id_error = QtWidgets.QCheckBox("Continuaza daca ID-ul difera")
        self.id_error.setToolTip("-y: nu opri operatia cand ID-ul citit difera "
                                 "de cel asteptat.")
        self.protect = QtWidgets.QCheckBox("Protejeaza dupa scriere")
        self.protect.setToolTip("-P: activeaza protectia de scriere dupa "
                                "operatie.")
        grid.addWidget(self.skip_id, 6, 0)
        grid.addWidget(self.id_error, 6, 1)
        grid.addWidget(self.protect, 6, 3)

        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        self.main_layout.addWidget(box)

    def _param_combo(self, tooltip):
        """Build one editable override combo filled from the chip database."""
        combo = QtWidgets.QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        combo.addItem(IMPLICIT)
        combo.setToolTip(tooltip)
        return combo

    def _free_override(self, tooltip):
        """Build one free text field that stays empty when it is unused."""
        field = QtWidgets.QLineEdit()
        field.setPlaceholderText(IMPLICIT)
        field.setToolTip(tooltip)
        return field


    # -------------------------------------------------------------- log area
    def _build_log_area(self):
        box = QtWidgets.QGroupBox("Jurnal")
        layout = QtWidgets.QVBoxLayout(box)
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.log.setMaximumBlockCount(5000)
        self.log.setPlaceholderText("iesirea din minipro apare aici")
        layout.addWidget(self.log)
        self.main_layout.addWidget(box, 1)

    # ------------------------------------------------------------- status row
    def _build_status_row(self):
        self.status_row = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(self.status_row)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.status = QtWidgets.QLabel("Pregatit.")
        self.cancel = QtWidgets.QPushButton("Anuleaza")
        self.cancel.clicked.connect(self.do_cancel)
        self.cancel.setEnabled(False)
        layout.addWidget(self.progress, 2)
        layout.addWidget(self.status, 2)
        layout.addWidget(self.cancel)
        self.main_layout.addWidget(self.status_row)

    # ------------------------------------------------------------------ menus
    def _build_menus(self):
        bar = self.menuBar()

        self.file_menu = bar.addMenu("Fisier")
        self.file_menu.addAction("Alege fisier...", self.choose_input_file)
        self.file_menu.addAction("Salveaza ca...", self.choose_output_file)
        self.file_menu.addSeparator()
        self.file_menu.addAction("Iesire", self.close)

        self.operations_menu = bar.addMenu("Operatii")
        self.operations_menu.addAction("Citeste", self.do_read)
        self.operations_menu.addAction("Verifica", self.do_verify)
        self.operations_menu.addAction("Verifica gol", self.do_blank)
        self.operations_menu.addAction("Citeste ID", self.do_id)
        self.operations_menu.addAction("Scrie", self.do_write)
        self.operations_menu.addAction("Sterge", self.do_erase)
        self.operations_menu.addAction("Verifica pini", self.do_pin_check)
        self.operations_menu.addSeparator()
        self.operations_menu.addAction("Test hardware", self.do_hardware_check)

        self.db_menu = bar.addMenu("Baza de date")
        self.db_menu.addAction("Listeaza cipurile", self.do_list_devices)
        self.db_menu.addAction("Cauta cip", self.do_search)
        self.db_menu.addAction("Informatii cip", self.do_chip_info)
        self.db_menu.addAction("Autodetectare SPI", self.do_autodetect)
        self.db_menu.addSeparator()
        self.db_menu.addAction("Programatori suportati",
                               self.do_supported_programmers)

        self.help_menu = bar.addMenu("Ajutor")
        self.help_menu.addAction("Despre", self.show_about)

    def _wire_runner(self):
        """Connect the engine signals to the log, bar and completion handler."""
        self.runner.progress.connect(self.on_progress)
        self.runner.log_line.connect(self.on_log_line)
        self.runner.done.connect(self.on_done)


    # ------------------------------------------------------- shared behaviour
    def _log(self, text):
        """Append one line to the journal."""
        self.log.appendPlainText(text)

    def on_progress(self, value):
        """Show a running call as a busy bar and a percent as a filled bar."""
        if value < 0:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(value)

    def on_log_line(self, text):
        """Show one minipro record in the journal."""
        self.log.appendPlainText(text)

    def _set_busy(self, busy):
        """Lock every control that could start a second minipro process."""
        for button in (self.read_button, self.verify_button, self.blank_button,
                       self.id_button, self.write_button, self.erase_button,
                       self.pin_button, self.info_button, self.list_button,
                       self.search_button, self.autodetect_button,
                       self.programmer_button, self.engine_button):
            button.setEnabled(not busy)
        self.cancel.setEnabled(busy)

    # -------------------------------------------------------- shared behaviour
    def selected_device(self):
        """Return the device name, or an empty string when none is chosen."""
        return self.devices.currentText().strip()

    def file_path(self):
        """Return the dump path exactly as it was typed."""
        return self.file.text().strip()

    def _refuse(self, title, text):
        """Report a missing requirement in the journal, then show it as a warning.

        The journal is the record an operator keeps of what this window did, so
        an action stopped before it reached the engine must be named there too
        and not only in a dialog box that can be dismissed unread.
        """
        self._log("%s: %s" % (title, text))
        QtWidgets.QMessageBox.warning(self, title, text)
        return None

    def _require_device(self):
        """Refuse a device operation when no device is selected."""
        device = self.selected_device()
        if not device:
            return self._refuse(
                "Dispozitiv lipsa",
                "Alege mai intai un dispozitiv din lista sau scrie-i numele.")
        return device

    def _require_file(self, output=False):
        """Refuse a file operation when the dump path is empty or unusable.

        output=True marks the path as something the engine will write: minipro
        opens a read target with "wb", which truncates whatever is already
        there, so an existing file needs an explicit confirmation first.
        """
        path = self.file.text().strip()
        if not path:
            return self._refuse("Fisier lipsa", "Scrie mai intai o cale de fisier.")
        if os.path.isdir(path):
            return self._refuse("Fisier invalid",
                                "%s este un director, nu un fisier dump." % path)
        if output:
            if os.path.exists(path) and not os.access(path, os.W_OK):
                return self._refuse("Fisier protejat",
                                    "%s exista si nu poate fi scris." % path)
            if os.path.exists(path) and not self._confirm_dump_overwrite(path):
                return None
        return path

    def _require_readable_file(self):
        """Refuse an action whose dump the engine must read from disk."""
        path = self._require_file()
        if not path:
            return None
        if not os.path.isfile(path):
            return self._refuse(
                "Fisier lipsa",
                "%s nu exista; aceasta operatie il citeste." % path)
        if not os.access(path, os.R_OK):
            return self._refuse("Fisier protejat", "%s nu poate fi citit." % path)
        return path

    def _confirm_dump_overwrite(self, path):
        """Ask before a read replaces a dump that already exists."""
        warning = ("Citirea va inlocui %s, care exista deja (%d octeti)."
                   % (path, os.path.getsize(path)))
        self._log(warning)
        answer = QtWidgets.QMessageBox.question(
            self, "Confirmare suprascriere",
            warning + "\n"
            "minipro deschide fisierul pentru scriere, deci continutul actual "
            "se pierde.\n\nContinui?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No)
        return answer == QtWidgets.QMessageBox.Yes


    # ------------------------------------------------------- command building
    def _op_args(self, action, device, path=None):
        """Build the argument list for one programming action."""
        args = ["-p", device]
        cfg_filter = self.cfg_section.currentData()
        if cfg_filter:
            # main.c sets page = CONFIG whenever --fuses, --uid or --lock is
            # given, so the region sent must be the one really programmed.
            if self.memory.currentData() and self.memory.currentData() != "config":
                self._log("Filtrul de config forțează -c config.")
            args += ["-c", "config", cfg_filter]
        elif self.memory.currentData():
            args += ["-c", self.memory.currentData()]
        if self.format.currentData():
            args += ["-f", self.format.currentData()]
        if self.advanced.isChecked():
            icsp = self.icsp.currentData()
            if icsp:
                args.append(icsp)
            if self.size_mode.currentData():
                args.append(self.size_mode.currentData())
            if self.unprotect.isChecked():
                args.append("-u")
            if self.protect.isChecked():
                args.append("-P")
            if self.skip_blank.isChecked():
                args.append("-B")
            if self.id_error.isChecked():
                args.append("-y")
            if self.skip_id.isChecked():
                if action in SKIP_ID_ACTIONS:
                    args.append("-x")
                else:
                    self._log("-x nu este permis pentru %s; il ignor."
                              % ACTION_LABELS[action])
            if action == "write":
                if self.skip_erase.isChecked():
                    args.append("-e")
                if self.skip_verify.isChecked():
                    args.append("-v")
        args.append(ACTION_FLAGS[action])
        if path is not None:
            args.append(path)
        return self._add_overrides(args)

    def _add_overrides(self, args):
        """Append the optional voltage, clock and current overrides."""
        if not self.advanced.isChecked():
            return args
        for combo, option in ((self.vcc, "--vcc"), (self.vdd, "--vdd"),
                              (self.vpp, "--vpp"), (self.spi_clock,
                                                   "--spi_clock")):
            value = combo.currentText().strip()
            if value and value != IMPLICIT:
                args.extend([option, value])
        field = self.pulse.text().strip()
        if field:
            args.extend(["--pulse", field])
        field = self.address.text().strip()
        if field:
            args.extend(["--address", field])
        level = self.ovc.currentData()
        if level:
            args.extend(["-o", "ovc_level=%d" % level])
        return args


    def _db_args(self, extra):
        """Build the argument list for one database-only action."""
        # The database named here is the one this query really reads, so it is
        # recorded here, at the moment the query is built.
        self.db_queried = self.db_filter.currentData()
        args = ["-q", self.db_queried]
        args.extend(extra)
        return args

    def _start(self, args, action, label):
        """Hand one command line to the engine and lock the controls."""
        if self.runner.running():
            QtWidgets.QMessageBox.information(
                self, "Proces in desfasurare",
                "Asteapta sfarsitul operatiei curente sau anuleaz-o.")
            return False
        self.expect = (action, label)
        self._set_busy(True)
        self.status.setText(label)
        if not self.runner.start(self.binary, args, label, self.database_home):
            self.expect = None
            self._set_busy(False)
            self.status.setText("Pregatit.")
            return False
        return True

    # ------------------------------------------------------ device operations
    def do_read(self):
        """Read one memory region into the dump file."""
        device = self._require_device()
        path = self._require_file(output=True)
        if not device or not path:
            return
        self._start(self._op_args("read", device, path), "read",
                    ACTION_TITLES["read"])

    def do_verify(self):
        """Compare one memory region with the dump file."""
        device = self._require_device()
        path = self._require_readable_file()
        if not device or not path:
            return
        self._start(self._op_args("verify", device, path), "verify",
                    ACTION_TITLES["verify"])

    def do_blank(self):
        """Check that the chip is blank."""
        device = self._require_device()
        if not device:
            return
        self._start(self._op_args("blank", device), "blank",
                    ACTION_TITLES["blank"])

    def do_id(self):
        """Read only the chip ID."""
        device = self._require_device()
        if not device:
            return
        self._start(self._op_args("id", device), "id",
                    ACTION_TITLES["id"])

    def do_write(self):
        """Write the dump after an explicit confirmation."""
        device = self._require_device()
        path = self._require_readable_file()
        if not device or not path:
            return
        if not self._confirm_write(path):
            return
        self._start(self._op_args("write", device, path), "write",
                    ACTION_TITLES["write"])

    def do_erase(self):
        """Erase the chip after two confirmations."""
        device = self._require_device()
        if not device:
            return
        if not self._confirm_erase():
            return
        self._start(self._op_args("erase", device), "erase",
                    ACTION_TITLES["erase"])

    def do_hardware_check(self):
        """Run the engine hardware check (-t)."""
        if not self._confirm("Test hardware",
                             "Testul hardware foloseste programatorul conectat."):
            return
        self._start(["-t"], "hardware", "Test hardware")

    def _confirm_write(self, path):
        """Ask once before a write, naming the options the engine will get."""
        text = ("Se va scrie %s in %s.\n\n%s" % (path, self.selected_device(),
                                                 self._override_summary("write")))
        # The operator may have asked for an option main.c drops for a write,
        # so the dialog has to say that instead of leaving it unmentioned.
        dropped = self._dropped_options("write")
        if dropped:
            text += "\n\n" + "\n".join(dropped)
        answer = QtWidgets.QMessageBox.question(
            self, "Confirmare scriere",
            text + "\n\nContinui?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No)
        return answer == QtWidgets.QMessageBox.Yes

    def _override_summary(self, action):
        """Name every option this command line really sends for one action."""
        parts = self._sent_options(action)
        if not parts:
            if self.advanced.isChecked():
                return "Optiuni avansate: niciuna trimisa."
            return ("Optiuni: niciuna, minipro alege regiunea si valorile "
                    "cipului.")
        return "Optiuni trimise: " + ", ".join(parts)

    def _sent_options(self, action):
        """Name every option _op_args really sends for one action."""
        parts = []
        cfg_filter = self.cfg_section.currentData()
        if cfg_filter:
            parts.append("-c config %s" % cfg_filter)
        elif self.memory.currentData():
            parts.append("-c %s" % self.memory.currentData())
        if self.format.currentData():
            parts.append("-f %s" % self.format.currentData())
        if not self.advanced.isChecked():
            return parts
        icsp = self.icsp.currentData()
        if icsp:
            parts.append(icsp)
        size = self.size_mode.currentData()
        if size:
            parts.append(size)
        for combo, option in ((self.vcc, "--vcc"), (self.vdd, "--vdd"),
                              (self.vpp, "--vpp"),
                              (self.spi_clock, "--spi_clock")):
            value = combo.currentText().strip()
            if value and value != IMPLICIT:
                parts.append("%s %s" % (option, value))
        for field, option in ((self.pulse, "--pulse"),
                              (self.address, "--address")):
            value = field.text().strip()
            if value:
                parts.append("%s %s" % (option, value))
        level = self.ovc.currentData()
        if level:
            parts.append("-o ovc_level=%d" % level)
        for box, option in ((self.unprotect, "-u"), (self.protect, "-P"),
                            (self.skip_blank, "-B"), (self.id_error, "-y")):
            if box.isChecked():
                parts.append(option)
        if self.skip_id.isChecked() and action in SKIP_ID_ACTIONS:
            # main.c reads -x only for these actions, so naming it for another
            # action would name an option _op_args never sends.
            parts.append("-x")
        if action == "write":
            # main.c only reads -e and -v as part of a write, so naming them
            # for another action would name an option never sent.
            for box, option in ((self.skip_erase, "-e"),
                                (self.skip_verify, "-v")):
                if box.isChecked():
                    parts.append(option)
        return parts

    def _dropped_options(self, action):
        """Name the options this window was asked for and will not send."""
        dropped = []
        if (self.advanced.isChecked() and self.skip_id.isChecked() and
                action not in SKIP_ID_ACTIONS):
            # _op_args only reaches this box when the advanced options are
            # open, so a box left closed behind an open group is not dropped:
            # it was never asked for in the first place.
            dropped.append("Optiunea \"Nu citi ID-ul\" nu este trimisa: "
                          "main.c o refuza pentru aceasta operatie.")
        return dropped

    def _confirm_erase(self):
        """Ask twice before an erase, naming the options it really sends.

        main.c never reads the chosen region when it erases: the ERASE action
        calls erase_device(), and that never reads the chosen page.  So the
        region shown here must not read as something that limits the erase.
        """
        warning = ("Stergerea distruge tot continutul cipului %s.\n%s\n"
                   "Regiunea aleasa nu limiteaza stergerea: main.c nu o "
                   "consultă când sterge."
                   % (self.selected_device(), self._override_summary("erase")))
        self._log(warning)
        first = QtWidgets.QMessageBox.warning(
            self, "Confirmare stergere",
            warning + "\n\nContinui?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No)
        if first != QtWidgets.QMessageBox.Yes:
            return False
        second = QtWidgets.QMessageBox.warning(
            self, "Confirmare finala",
            "Ultima avertizare: dupa stergere datele nu mai pot fi recuperate.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No)
        return second == QtWidgets.QMessageBox.Yes

    def _confirm(self, title, text):
        """Ask one yes/no question and report the answer."""
        answer = QtWidgets.QMessageBox.warning(
            self, title, text, QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No)
        return answer == QtWidgets.QMessageBox.Yes

    # ------------------------------------------------------- database queries
    def do_list_devices(self):
        """List every chip of the selected database."""
        self._start(self._db_args(["-l"]), "list", "Listare cipurile")

    def do_search(self):
        """Search the selected database for one chip name."""
        term = self.search.text().strip()
        if not term:
            QtWidgets.QMessageBox.information(self, "Cautare",
                                              "Scrie mai intai un nume de cip.")
            return
        self._start(self._db_args(["-L", term]), "search",
                    "Cautare %s" % term)

    def do_chip_info(self):
        """Show the database record of the selected chip."""
        device = self._require_device()
        if not device:
            return
        self._start(self._db_args(["-d", device]), "info",
                    "Informatii %s" % device)

    def do_autodetect(self):
        """Autodetect an SPI chip in the selected package."""
        if not self._confirm("Autodetectare SPI",
                             "Autodetectarea citeste cipul din socket."):
            return
        self._start(["-a", self.autodetect_combo.currentData()], "autodetect",
                    "Autodetectare SPI")

    def do_supported_programmers(self):
        """List the programmer families the engine claims to support."""
        self._start(["-Q"], "supported", "Programatori suportati")

    def do_pin_check(self):
        """Run the pin contact check."""
        device = self._require_device()
        if not device:
            return
        self._start(self._op_args("pin", device), "pin",
                    ACTION_TITLES["pin"])


    # ------------------------------------------------------------- completion
    def on_done(self, exit_code, stdout_text):
        """Report one finished call and refresh the views it filled."""
        action, label = self.expect or ("none", "Operatie")
        self.expect = None
        self._set_busy(False)
        if self.runner.cancelled:
            self.status.setText("Operatie anulata.")
            self._log("%s anulata (cod %d)." % (label, exit_code))
            return
        if exit_code == 0:
            self.status.setText("%s terminata." % label)
            self._log("%s terminata cu succes." % label)
        else:
            self.status.setText("%s esuata (cod %d)." % (label, exit_code))
            self._log("%s esuata, cod de iesire %d." % (label, exit_code))
        if exit_code != 0:
            # A failed call produced no listing and no chip record, so anything
            # still on screen comes from an earlier call that did succeed.
            if action in ("list", "search", "autodetect", "info", "version",
                          "presence"):
                self._log("Vederile ramane cele ale unei operatii anterioare: "
                          "aceasta nu a produs date.")
            return
        if action in ("list", "search", "autodetect"):
            self.apply_device_list(stdout_text)
        elif action == "info":
            self.apply_chip_info(self.runner.stderr_text)
        elif action == "version":
            self.apply_engine_info(self.runner.stderr_text)
        elif action == "presence":
            self.apply_programmer(self.runner.stderr_text)

    def apply_device_list(self, text):
        """Fill the device combo from one minipro listing, keeping the choice."""
        names = [line.strip() for line in text.splitlines() if line.strip()]
        if not names:
            # An empty listing must not leave the previous list on screen: an
            # operator would read chips that this call never reported.
            self.devices.blockSignals(True)
            self.devices.clear()
            self.devices.blockSignals(False)
            self._on_chip_context_changed()
            self._log("Niciun cip gasit in baza de date selectata.")
            return
        current = self.devices.currentText()
        self.devices.blockSignals(True)
        self.devices.clear()
        for name in names:
            self.devices.addItem(name)
        position = self.devices.findText(current)
        if position >= 0:
            self.devices.setCurrentIndex(position)
        self.devices.blockSignals(False)
        # The refill kept the signals closed, so the chip context is checked
        # here instead: a list that no longer holds the old chip invalidates it.
        self._on_chip_context_changed()
        self._log("%d dispozitive in lista." % len(names))

    def _device_from_line(self, line):
        """Return the chip name one autodetect or info record names."""
        line = line.strip()
        if line.startswith("Name:"):
            return line[len("Name:"):].strip()
        return line

    def apply_chip_info(self, text):
        """Fill the override lists from one chip record.

        The record itself was already streamed to the journal record by record
        while the engine wrote it, so the journal is kept intact here.  The
        lists filled here belong to the chip this record names: they are remembered
        as belonging to it so a later chip change drops them.
        """
        text = strip_ansi(text)
        chip = None
        for line in text.splitlines():
            if line.startswith("Name:"):
                name = self._device_from_line(line)
                if not name:
                    continue
                chip = name
                if self.devices.findText(name) < 0:
                    self.devices.addItem(name)
                self.devices.setCurrentIndex(self.devices.findText(name))
        for combo, header in ((self.spi_clock,
                               "Available SPI clock frequencies [MHz]:"),
                              (self.vpp, "Available VPP voltages [V]:"),
                              (self.vdd, "Available VDD write voltages [V]:"),
                              (self.vcc, "Available VCC verify voltages [V]:")):
            self._fill_combo(combo, parse_available_values(text, header))
        self.chip_overrides_for = chip or self.selected_device()
        # The record was queried from the database named when the call started,
        # not from whichever one is selected now: db_filter stays reachable
        # while the engine runs, so reading it here would attribute the values
        # of one database to another.
        self.chip_overrides_from = self.db_queried

    def _on_chip_context_changed(self, *args):
        """Drop chip values when the chip or the database behind them changes."""
        if self.chip_overrides_for is None:
            return
        if self.chip_overrides_for != self.selected_device():
            self._reset_chip_overrides("cipul selectat s-a schimbat")
        elif self.chip_overrides_from != self.db_filter.currentData():
            self._reset_chip_overrides("baza de date selectata s-a schimbat")

    def _reset_chip_overrides(self, reason):
        """Return every chip-specific override to the database default."""
        for combo in (self.vcc, self.vdd, self.vpp, self.spi_clock):
            self._fill_combo(combo, ())
        self.chip_overrides_for = None
        self.chip_overrides_from = None
        self._log("Suprascrierile cipului au fost resetate: %s." % reason)

    def _fill_combo(self, combo, values):
        """Fill one override combo with the values of the selected chip."""
        combo.blockSignals(True)
        while combo.count() > 1:
            combo.removeItem(1)
        for value in values:
            combo.addItem(value)
        combo.setCurrentIndex(0)
        combo.blockSignals(False)


    def apply_engine_info(self, text):
        """Show what -V reported about the engine and its databases."""
        text = strip_ansi(text)
        self.engine_version = parse_version(text)
        self.engine_commit = parse_commit(text)
        self.engine_counts = parse_chip_counts(text)
        self.engine_label.setText("%s  (commit %s)" % (self.engine_version or "?",
                                                  self.engine_commit or "?"))
        counts = ", ".join("%s: %d" % (key, value)
                           for key, value in sorted(self.engine_counts.items()))
        self.db_count_label.setText(counts)

    def apply_programmer(self, text):
        """Show which programmer -k found, or say that none is connected."""
        found = parse_programmer(text)
        if found:
            self.programmer_label.setText(found)
        else:
            self.programmer_label.setText("niciun programator gasit")

    def refresh_programmer(self):
        """Ask the engine which programmer is connected right now."""
        self._start(["-k"], "presence", "Detectare programator")

    def show_engine_info(self):
        """Ask the engine for its version, commit and database sizes."""
        self._start(["-V"], "version", "Informatii motor")

    def show_about(self):
        """Explain what this front-end does and what it does not do."""
        QtWidgets.QMessageBox.information(
            self, "Despre",
            "MiniPro GUI este o interfata pentru utilitarul in linie de comanda "
            "minipro.\n\nFront-endul nu discuta direct cu programatorul: "
            "construieste o comanda, o ruleaza cu QProcess si arata progresul, "
            "jurnalul, listele de cipuri si informatiile din baza de date.\n\n"
            "Motor: %s\nVersiune: %s\nCommit: %s" % (self.binary,
                                                    self.engine_version or "?",
                                                    self.engine_commit or "?"))


    # ------------------------------------------------------------ file dialog
    def choose_input_file(self):
        """Choose the dump file through the native open dialog."""
        path = QtWidgets.QFileDialog.getOpenFileName(
            self, "Alege fisier dump", os.path.dirname(self.file_path()) or ".",
            "Fisiere dump (*.bin *.hex *.srec *.s19);;Toate fisierele (*)")
        path = path[0] if isinstance(path, tuple) else path
        if path:
            self.file.setText(path)
            self.update_file_info()

    def choose_output_file(self):
        """Set the dump path through the native save dialog."""
        path = QtWidgets.QFileDialog.getSaveFileName(
            self, "Salveaza dump ca", self.file_path(),
            "Fisier dump (*.bin);;Toate fisierele (*)")
        path = path[0] if isinstance(path, tuple) else path
        if path:
            self.file.setText(path)
            self.update_file_info()

    def update_file_info(self):
        """Report the size of the chosen dump file, or why it is unusable."""
        path = self.file_path()
        if not path:
            self.file_info.setText("fara fisier")
            return
        if os.path.isdir(path):
            self.file_info.setText("calea este un director")
        elif os.path.isfile(path):
            self.file_info.setText("%d octeti" % os.path.getsize(path))
        else:
            self.file_info.setText("fisier inexistent")

    # ------------------------------------------------------------- cancel/close
    def do_cancel(self):
        """Ask the engine to stop the operation that is running."""
        if not self.runner.cancel():
            self.status.setText("Nu exista nicio operatie in desfasurare.")

    def closeEvent(self, event):
        """Stop the engine, and only close once it really stopped."""
        if self.runner.running():
            answer = QtWidgets.QMessageBox.question(
                self, "Iesire",
                "O operatie minipro ruleaza inca. Anulez operatia si ies?\n\n"
                "Daca alegi Nu, fereastra ramane deschisa.",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No)
            if answer != QtWidgets.QMessageBox.Yes:
                event.ignore()
                return
            self.runner.cancel()
            if not self._await_process_exit():
                QtWidgets.QMessageBox.critical(
                    self, "Iesire refuzata",
                    "Procesul minipro nu s-a oprit nici dupa kill. "
                    "Fereastra ramane deschisa ca sa nu il pierzi.")
                event.ignore()
                return
        event.accept()

    def _await_process_exit(self):
        """Wait for the cancelled engine, escalating to a kill once."""
        if self._wait_for_stop(CLOSE_GRACE_MS):
            return True
        self.runner.force_kill()
        return self._wait_for_stop(CLOSE_KILL_GRACE_MS)

    def _wait_for_stop(self, timeout_ms):
        """Pump the event loop until the engine call is gone."""
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            QtWidgets.QApplication.processEvents()
            if not self.runner.running():
                return True
            time.sleep(0.02)
        return not self.runner.running()


def main():
    """Start the front-end, pointing it at the engine it should drive."""
    parser = argparse.ArgumentParser(
        description="Interfata grafica pentru utilitarul minipro.")
    parser.add_argument("--binary", help="calele executabilului minipro")
    options = parser.parse_args()
    try:
        binary = find_binary(options.binary)
    except BinaryNotFound as error:
        sys.stderr.write("%s\n" % error)
        return 2
    app = QtWidgets.QApplication(sys.argv)
    window = MiniproGui(binary)
    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())

