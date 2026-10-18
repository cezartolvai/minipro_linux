#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# minipro_gui.py - PyQt5 front-end for the minipro command line.
#
# This file is a part of Minipro.
#
# Minipro is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# Minipro is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
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


# ------------------------------------------------------------------- language
# Every string this front-end shows lives in one table, keyed by where it is
# shown, and every key carries both languages this front-end speaks.  English
# is what a fresh window shows; Romana is what it shows once the operator picks
# it in View.  Keeping the two languages side by side in one row is what keeps
# an untranslated key from surviving a change: a row is obviously unfinished
# when its second half is missing.
DEFAULT_LANGUAGE = "en"

# Offered by the View menu.  Each name is written in the language it names.
LANGUAGE_CHOICES = (
    ("en", "English"),
    ("ro", "Romana"),
)

# key -> (English, Romana)
TEXTS = {
    # ------------------------------------------------------- window and banner
    "window.title": ("MiniPro GUI", "MiniPro GUI"),
    "banner.experimental": (
        "Experimental interface: this front-end is tested headlessly, not "
        "against every programmer and chip. Back up the chip before a write "
        "or an erase.",
        "Interfata experimentala: acest front-end este testat prin suita de "
        "regresie, nu pe fiecare programator si cip. Fa o copie a cipului "
        "inainte de o scriere sau o stergere.",
    ),
    "about.experimental": (
        "This front-end is experimental: its behaviour is covered by an "
        "automated suite, not by a pass over every programmer and chip. Back "
        "up the chip before a write or an erase.",
        "Acest front-end este experimental: comportamentul lui este acoperit "
        "de o suita automata, nu de o verificare pe fiecare programator si "
        "cip. Fa o copie a cipului inainte de o scriere sau o stergere.",
    ),
    # ------------------------------------------------------------------ header
    "header.box": ("Programmer and engine", "Programator si motor"),
    "header.programmer": ("Connected programmer:", "Programator conectat:"),
    "header.engine": ("minipro engine:", "Motor minipro:"),
    "header.checking": ("checking...", "in verificare..."),
    "header.reconnect": ("Reconnect", "Reconecteaza"),
    "header.engine_details": ("Engine details", "Detalii motor"),
    "header.no_programmer": ("no programmer found",
                            "niciun programator gasit"),
    # ------------------------------------------------------------------ device
    "box.device": ("Device", "Dispozitiv"),
    "device.database": ("Database (listing / info):",
                        "Baza de date (listare / info):"),
    "box.device_tip": (
        "Choose the database -q queries for listing, searching and chip "
        "information (-l, -L, -d). It is never sent to a programming "
        "operation: there minipro uses the programmer found on USB.",
        "Alege baza de date interogata de -q la listare, cautare si\n"
        "informatii cip (-l, -L, -d). Nu este trimis niciodata la o\n"
        "operatie de programare: acolo minipro foloseste programatorul\n"
        "gasit pe USB.",
    ),
    "device.search_placeholder": ("ex. W25Q16, AT28C256, ATmega328P",
                                  "ex. W25Q16, AT28C256, ATmega328P"),
    "action.search": ("Search chip", "Cauta cip"),
    "box.device_device": ("Device (-p):", "Dispozitiv (-p):"),
    "box.device_name_tip": ("Exact name from the database, usually "
                            "NAME@PACKAGE",
                            "Nume exact din baza de date, de obicei "
                            "NUME@CAPSULA"),
    "box.info_button": ("Chip information", "Informatii cip"),
    "autodetect.spi8": ("SPI 8 pin", "SPI 8 pini"),
    "autodetect.spi16": ("SPI 16 pins", "SPI 16 pini"),
    "box.autodetect_button": ("Autodetect SPI", "Autodetectare SPI"),
    "box.list_button": ("List chips", "Listeaza cipurile"),
    # ------------------------------------------------------------------- file
    "box.file": ("Dump file and memory", "Fisier dump si memorie"),
    "file.path": ("Dump file:", "Fisier dump:"),
    "file.open": ("Open...", "Deschide..."),
    "file.save": ("Save as...", "Salveaza ca..."),
    "file.memory": ("Memory (-c):", "Memorie (-c):"),
    "config.region": ("Config region:", "Regiune config:"),
    "file.format": ("File format (-f):", "Format fisier (-f):"),
    "file.none": ("no file", "fara fisier"),
    "file.is_dir": ("the path is a directory", "calea este un director"),
    "file.size": ("%d bytes", "%d octeti"),
    "file.missing": ("file does not exist", "fisier inexistent"),
    "file.memory_tip": ("-c: the memory region.  Without -c minipro picks the "
                        "single region itself, and that default can differ "
                        "between a read and a write.",
                        "-c: regiunea de memorie. Fara -c minipro alege\n"
                        "singur regiunea, iar comportamentul implicit\n"
                        "poate diferi intre citire si scriere."),
    "file.config_tip": ("--fuses / --uid / --lock: limits the operation to "
                        "fuses, user id or lock bits.\nAll three force -c "
                        "config.",
                        "--fuses / --uid / --lock: limiteaza\n"
                        "operatia la fuse-uri, user id sau lock bits.\n"
                        "Toate trei forteaza -c config."),
    "file.format_tip": ("-f: the file format used by -r, -w and -m.",
                        "-f: format de fisier pentru -r, -w si -m."),
    # ------------------------------------------------------------ operations
    "box.operations": ("Operations", "Operatii"),
    "action.read": ("Read", "Citeste"),
    "action.verify": ("Verify", "Verifica"),
    "action.blank": ("Check blank", "Verifica gol"),
    "action.id": ("Read ID", "Citeste ID"),
    "action.write": ("Write", "Scrie"),
    "action.erase": ("Erase", "Sterge"),
    "action.pin": ("Check pins", "Verifica pini"),
    "action.hardware": ("Hardware test", "Test hardware"),
    # -------------------------------------------------------------- advanced
    "box.advanced": ("Advanced options", "Optiuni avansate"),
    "advanced.icsp": ("ICSP mode:", "Mod ICSP:"),
    "advanced.vcc": ("VCC (override):", "VCC (override):"),
    "advanced.vpp": ("VPP (override):", "VPP (override):"),
    "advanced.vdd": ("VDD (override):", "VDD (override):"),
    "advanced.spi_clock": ("SPI clock (MHz):", "SPI clock (MHz):"),
    "advanced.pulse": ("Programming pulse (us):",
                       "Impuls programare (us):"),
    "advanced.i2c": ("I2C address:", "Adresa I2C:"),
    "advanced.ovc": ("Overcurrent level:", "Nivel supracurent:"),
    "advanced.file_size": ("File size:", "Marime fisier:"),
    "advanced.skip_erase": ("Skip the erase (-e)", "Sari peste stergere (-e)"),
    "advanced.skip_verify": ("Do not verify after writing",
                            "Nu verifica dupa scriere"),
    "advanced.skip_blank": ("Skip blank blocks", "Sari peste blocurile goale"),
    "advanced.unprotect": ("Unprotect writing", "Deblocheaza scrierea"),
    "advanced.skip_id": ("Do not read the ID", "Nu citi ID-ul"),
    "advanced.id_error": ("Continue when the ID differs",
                          "Continuaza daca ID-ul difera"),
    "advanced.protect": ("Protect after writing", "Protejeaza dupa scriere"),
    "advanced.icsp_tip": ("-i: ICSP with VCC, -I: ICSP without VCC.  Without "
                          "them minipro programs through the socket.",
                          "-i: ICSP cu VCC, -I: ICSP fara VCC. Fara ele "
                          "minipro programeaza prin socket."),
    "advanced.vcc_tip": ("--vcc: the read and verify voltage in volts.\n"
                         "Default: the value from the chip's database.",
                         "--vcc: tensiune de citire si verificare "
                         "in volti.\nImplicit: valoarea din baza "
                         "de date a cipului."),
    "advanced.vpp_tip": ("--vpp: the programming voltage in volts, for EPROM, "
                        "EEPROM and MCU.",
                         "--vpp: tensiune de programare in volti, "
                         "pentru EPROM, EEPROM si MCU."),
    "advanced.vdd_tip": ("--vdd: the writing voltage in volts.",
                         "--vdd: tensiune de scriere in volti."),
    "advanced.spi_clock_tip": ("--spi_clock: the SPI frequency in MHz.  The "
                             "accepted values depend on the chip: press\n"
                             "\"Chip information\" so this list gets filled "
                             "from the database.",
                             "--spi_clock: frecventa SPI in MHz. Valorile "
                             "acceptate depind de cip: apasa\n\"Informatii "
                             "cip\" ca lista de aici sa fie completata "
                             "din baza de date."),
    "advanced.pulse_tip": ("--pulse: the programming pulse length in "
                         "microseconds (1-65535).",
                         "--pulse: durata impulsului de "
                         "programare in microsecunde (1-65535)."),
    "advanced.i2c_tip": ("--address: the I2C address, decimal or 0x..., for "
                         "I2C chips.",
                         "--address: adresa I2C, zecimala "
                         "sau 0x..., pentru cipuri I2C."),
    "advanced.ovc_tip": ("-o ovc_level=N: the programmer's current limit.\n"
                         "Default: the limit the firmware picks.",
                         "-o ovc_level=N: limita de curent a "
                         "programatorului.\nImplicit: limita aleasa de "
                         "firmware."),
    "advanced.file_size_tip": ("-s: ignore a size difference between file and "
                               "memory.\n-S: main.c sets both size_nowarn and "
                               "size_error, so a different size is\nnot merely "
                               "reported later: the operation continues with "
                               "it.",
                               "-s: ignora diferenta de marime dintre "
                               "fisier si memorie.\n-S: main.c seteaza atat "
                               "size_nowarn cat si size_error, deci o "
                               "marime diferita\nnu este doar anuntata mai "
                               "tarziu: operatia continua cu ea."),
    "advanced.skip_erase_tip": ("-e: do not erase before writing.",
                               "-e: nu sterge inainte de scriere."),
    "advanced.skip_verify_tip": ("-v: skip the verify that follows a write.",
                                 "-v: sari peste verificarea de dupa "
                                 "scriere."),
    "advanced.skip_blank_tip": ("-B: skip the blocks that are already blank.",
                                "-B: sare peste blocurile care sunt goale."),
    "advanced.unprotect_tip": ("-u: remove write protection before the "
                               "operation.",
                               "-u: scoate protectia de scriere inainte de "
                               "operatie."),
    "advanced.skip_id_tip": ("-x: do not read the ID before the operation.\n"
                             "main.c refuses the skip for write, erase and ID "
                             "read, so there it is ignored.",
                             "-x: nu citi ID-ul inainte de operatie.\n"
                             "main.c refuza saltul la scriere, stergere si "
                             "citire de ID, deci acolo este ignorat."),
    "advanced.id_error_tip": ("-y: do not stop the operation when the ID read "
                              "differs from the one expected.",
                              "-y: nu opri operatia cand ID-ul citit difera "
                              "de cel asteptat."),
    "advanced.protect_tip": ("-P: enable write protection after the "
                              "operation.",
                              "-P: activeaza protectia de scriere dupa "
                              "operatie."),
    # ------------------------------------------------------------- log, status
    "box.log": ("Journal", "Jurnal"),
    "log.placeholder": ("minipro output appears here",
                        "iesirea din minipro apare aici"),
    "status.ready": ("Ready.", "Pregatit."),
    "status.cancelled": ("Operation cancelled.", "Operatie anulata."),
    "status.done": ("%s finished.", "%s terminata."),
    "status.failed": ("%s failed (code %d).", "%s esuata (cod %d)."),
    "status.no_operation": ("There is no operation running.",
                            "Nu exista nicio operatie in desfasurare."),
    "action.cancel": ("Cancel", "Anuleaza"),
    # ------------------------------------------------------------------ menus
    "menu.file": ("File", "Fisier"),
    "menu.database": ("Database", "Baza de date"),
    "menu.operations": ("Operations", "Operatii"),
    "menu.view": ("View", "Vedere"),
    "menu.help": ("Help", "Ajutor"),
    "menu.choose_file": ("Choose a file...", "Alege fisier..."),
    "menu.save_as": ("Save as...", "Salveaza ca..."),
    "menu.quit": ("Quit", "Iesire"),
    "menu.list_chips": ("List chips", "Listeaza cipurile"),
    "menu.search_chip": ("Search chip", "Cauta cip"),
    "menu.chip_info": ("Chip information", "Informatii cip"),
    "menu.autodetect": ("Autodetect SPI", "Autodetectare SPI"),
    "menu.supported_programmers": ("Supported programmers",
                                    "Programatori suportati"),
    "menu.about": ("About", "Despre"),
    "menu.language": ("Language", "Limba"),

    # ------------------------------------------------- override field labels
    "field.implicit": ("Implicit (the chip's value)",
                       "Implicit (valoarea din cip)"),
    "memory.implicit": ("Implicit (minipro picks)",
                       "Implicit (alege minipro)"),
    "memory.code": ("code", "code"),
    "memory.data": ("data", "data"),
    "memory.config": ("config", "config"),
    "memory.user": ("user", "user"),
    "memory.calibration": ("calibration", "calibration"),
    "word.read": ("read", "citire"),
    "word.write": ("write", "scriere"),
    "word.verify": ("verify", "verificare"),
    "word.erase": ("erase", "stergere"),
    "word.blank": ("blank check", "verificare gol"),
    "word.id": ("ID read", "citire ID"),
    "word.pin": ("pin check", "verificare pini"),
    "title.read": ("Read", "Citire"),
    "title.write": ("Write", "Scriere"),
    "title.verify": ("Verify", "Verificare"),
    "title.erase": ("Erase", "Stergere"),
    "title.blank": ("Blank check", "Verificare gol"),
    "title.id": ("ID read", "Citire ID"),
    "title.pin": ("Pin check", "Verificare pini"),
    "title.operation": ("Operation", "Operatie"),
    "config.all": ("The whole config region", "Toata regiunea config"),
    "config.fuses": ("only the fuses", "doar fuse-urile"),
    "config.uid": ("only the user id", "doar user id"),
    "config.lock": ("only the lock bits", "doar lock bits"),
    "format.auto": ("Auto (binary)", "Auto (binar)"),
    "format.ihex": ("Intel HEX", "Intel HEX"),
    "format.srec": ("S-Record", "S-Record"),
    "ovc.implicit": ("Implicit (from the firmware)",
                     "Implicit (din firmware)"),
    "ovc.level": ("%d mA", "%d mA"),
    "icsp.standard": ("Standard mode (no ICSP)", "Mod standard (fara ICSP)"),
    "icsp.with_vcc": ("ICSP with VCC (-i)", "ICSP cu VCC (-i)"),
    "icsp.no_vcc": ("ICSP without VCC (-I)", "ICSP fara VCC (-I)"),
    "size.error": ("Error on a size difference",
                   "Eroare la diferenta de marime"),
    "size.ignore": ("Ignore a size difference (-s)",
                    "Ignora diferenta de marime (-s)"),
    "size.continue": ("Continue with another size, without a warning (-S)",
                      "Continua cu alta marime, fara avertisment (-S)"),
    # ------------------------------------------------- refusals and confirmations
    "refusal.device.title": ("Missing device", "Dispozitiv lipsa"),
    "refusal.device.body": ("Pick a device from the list first, or write its "
                            "name.",
                            "Alege mai intai un dispozitiv din lista sau "
                            "scrie-i numele."),
    "refusal.file.title": ("Missing file", "Fisier lipsa"),
    "refusal.file.body": ("Write a file path first.",
                          "Scrie mai intai o cale de fisier."),
    "refusal.unreadable.title": ("Unusable file", "Fisier invalid"),
    "refusal.unreadable.body": ("%s cannot be read.", "%s nu poate fi citit."),
    "refusal.unwritable.title": ("Protected file", "Fisier protejat"),
    "refusal.unwritable.body": ("%s cannot be written.",
                                "%s nu poate fi scris."),
    "refusal.running.title": ("Process running", "Proces in desfasurare"),
    "refusal.running.body": ("Wait for the current operation to end, or cancel "
                             "it.",
                             "Asteapta sfarsitul operatiei curente sau "
                             "anuleaz-o."),
    "confirm.overwrite.title": ("Overwrite confirmation",
                                "Confirmare suprascriere"),
    "confirm.overwrite.body": ("Reading will replace %s, which already exists "
                               "(%d bytes).",
                               "Citirea va inlocui %s, care exista deja "
                               "(%d octeti)."),
    "confirm.write.title": ("Write confirmation", "Confirmare scriere"),
    "confirm.write.body": ("%s will be written into %s.\n\n%s",
                           "Se va scrie %s in %s.\n\n%s"),
    "confirm.erase.title": ("Erase confirmation", "Confirmare stergere"),
    "confirm.erase.body": ("The erase destroys the whole contents of chip %s."
                            "\n%s\nThe region chosen does not limit the erase: "
                            "main.c does not consult it when it erases.",
                            "Stergerea distruge tot continutul cipului %s.\n%s"
                            "\nRegiunea aleasa nu limiteaza stergerea: main.c "
                            "nu o consulta cand sterge."),
    "confirm.final.title": ("Final confirmation", "Confirmare finala"),
    "confirm.final.body": ("Last warning: after an erase the data cannot be "
                            "recovered.",
                            "Ultima avertizare: dupa stergere datele nu mai "
                            "pot fi recuperate."),
    "confirm.hardware.title": ("Hardware test", "Test hardware"),
    "confirm.hardware.body": ("The hardware test uses the connected "
                              "programmer.",
                              "Testul hardware foloseste programatorul "
                              "conectat."),
    "confirm.autodetect.title": ("Autodetect SPI", "Autodetectare SPI"),
    "confirm.autodetect.body": ("Autodetection reads the chip sitting in the "
                                "socket.",
                                "Autodetectarea citeste cipul din socket."),
    "confirm.options_none": ("Options: none, minipro picks the region and the "
                            "values itself.",
                            "Optiuni: niciuna, minipro alege regiunea si "
                            "valorile singur."),
    "confirm.options_advanced_none": ("Advanced options: none sent.",
                                    "Optiuni avansate: niciuna trimisa."),
    "confirm.options_sent": ("Options sent: ", "Optiuni trimise: "),
    "confirm.options_dropped": ("The \"Do not read the ID\" option is not "
                                "sent: main.c rejects it for this action.",
                                "Optiunea \"Nu citi ID-ul\" nu este trimisa: "
                                "main.c o respinge pentru aceasta operatie."),
    # ------------------------------------------------------------- runner text
    "runner.cancel": ("Cancelling: stopping minipro...",
                      "Anulare: se opreste minipro..."),
    "runner.no_start": ("The minipro process could not be started.",
                       "Nu am putut porni procesul minipro."),
    "runner.timeout": ("The minipro process did not answer in time.",
                      "Procesul minipro nu a raspuns la timp."),
    "runner.truncated": ("The journal of the operation was truncated to %d "
                         "characters.",
                         "Jurnalul operatiei a fost trunchiat la %d "
                         "caractere."),
    # ------------------------------------------------------------- journal text
    "journal.db_source": ("The databases are read from %s.",
                          "Bazele de date sunt citite din %s."),
    "journal.config_forces": ("The config filter forces -c config.",
                          "Filtrul de config forteaza -c config."),
    "journal.stale_views": ("The views still show an earlier operation: this "
                            "one produced no data.",
                            "Vederile ramane cele ale unei operatii "
                            "anterioare: aceasta nu a produs date."),
    "journal.no_chips": ("No chip found in the selected database.",
                         "Niciun cip gasit in baza de date selectata."),
    "journal.overrides_reset": ("The chip overrides were reset: %s.",
                               "Suprascrierile cipului au fost resetate: %s."),
    "journal.skip_id": ("-x is not allowed for %s; ignoring it.",
                        "-x nu este permis pentru %s; il ignor."),
    "journal.device_count": ("%d devices in the list.",
                             "%d dispozitive in lista."),
    "journal.reset_chip": ("the selected chip changed",
                           "cipul selectat s-a schimbat"),
    "journal.reset_database": ("the selected database changed",
                               "baza de date selectata s-a schimbat"),
    "journal.cancelled": ("%s cancelled (code %d).", "%s anulata (cod %d)."),
    "journal.done": ("%s finished successfully.", "%s terminata cu succes."),
    "journal.failed": ("%s failed, exit code %d.",
                       "%s esuata, cod de iesire %d."),
    "refusal.directory.body": ("%s is a directory, not a dump file.",
                               "%s este un director, nu un fisier dump."),
    "refusal.missing.body": ("%s does not exist; this operation reads it.",
                             "%s nu exista; aceasta operatie il citeste."),
    "confirm.overwrite.more": ("minipro opens the file for writing, so the "
                              "current content is lost.\n\nContinue?",
                              "minipro deschide fisierul pentru scriere, deci "
                              "continutul actual se pierde.\n\nContinui?"),
    "confirm.continue": ("\n\nContinue?", "\n\nContinui?"),
    # ------------------------------------------------- engine lookup and about
    "lookup.explicit": ("minipro was not found at: %s",
                        "Nu am gasit minipro la: %s"),
    "lookup.missing": ("The minipro executable was not found. Build it or "
                       "point at it with --binary <path>.",
                       "Nu am gasit executabilul minipro. Constructeaza-l "
                       "sau arata-l cu --binary <cale>."),
    "about.title": ("About", "Despre"),
    "about.body": ("MiniPro GUI is a front-end for the minipro command line "
                   "utility.\n\nThe front-end never talks to the programmer "
                   "directly: it builds one command, runs it with QProcess "
                   "and shows the progress, the journal, the chip listings "
                   "and the database information.\n\n%s\n\n%s",
                   "MiniPro GUI este o interfata pentru utilitarul in linie "
                   "de comanda minipro.\n\nFront-endul nu discuta direct cu "
                   "programatorul: construieste o comanda, o ruleaza cu "
                   "QProcess si arata progresul, jurnalul, listele de cipuri "
                   "si informatiile din baza de date.\n\n%s\n\n%s"),
    "close.title": ("Quit", "Iesire"),
    "close.body": ("A minipro operation is still running. Cancel it and quit?\n\n"
                   "Choosing No leaves the window open.",
                   "O operatie minipro ruleaza inca. Anulez operatia si ies?\n\n"
                   "Daca alegi Nu, fereastra ramane deschisa."),
    "close.refused_title": ("Quit refused", "Iesire refuzata"),
    "close.refused_body": ("The minipro process did not stop even after a "
                           "kill. The window stays open so you do not lose "
                           "it.",
                           "Procesul minipro nu s-a oprit nici dupa kill. "
                           "Fereastra ramane deschisa ca sa nu il pierzi."),
    "engine.commit": ("%s  (commit %s)", "%s  (commit %s)"),
    "about.engine": ("Engine: %s\nVersion: %s\nCommit: %s",
                     "Motor: %s\nVersiune: %s\nCommit: %s"),
    # ------------------------------------------------------------- search text
    "search.title": ("Search", "Cautare"),
    "search.empty": ("Write a chip name first.",
                     "Scrie mai intai un nume de cip."),
    "task.search": ("Search %s", "Cautare %s"),
    "task.chip_info": ("Information %s", "Informatii %s"),
    # ------------------------------------------------------------- file dialogs
    "file_dialog.input_title": ("Choose a dump file", "Alege fisier dump"),
    "file_dialog.output_title": ("Save the dump as", "Salveaza dump ca"),
    "file_dialog.input_filters": ("Dump files (*.bin *.hex *.srec *.s19);;"
                                 "All files (*)",
                                 "Fisiere dump (*.bin *.hex *.srec *.s19);;"
                                 "Toate fisierele (*)"),
    "file_dialog.output_filters": ("Dump file (*.bin);;All files (*)",
                                   "Fisier dump (*.bin);;Toate fisierele (*)"),
    # ------------------------------------------------- start labels and labels
    "task.list": ("Listing chips", "Listare cipurile"),
    "task.autodetect": ("Autodetect SPI", "Autodetectare SPI"),
    "task.supported": ("Supported programmers", "Programatori suportati"),
    "task.hardware": ("Hardware test", "Test hardware"),
    "task.presence": ("Programmer detection", "Detectare programator"),
    "task.version": ("Engine information", "Informatii motor"),
    # ------------------------------------------------------------- main() text
    "main.description": ("A graphical front-end for the minipro utility.",
                         "Interfata grafica pentru utilitarul minipro."),
    "main.binary_help": ("path of the minipro executable",
                         "calea executabilului minipro"),
}

# Which half of a TEXTS row one language reads.
LANGUAGE_INDEX = {"en": 0, "ro": 1}


def text_for(key, language=DEFAULT_LANGUAGE):
    """Return one keyed display string in one named language."""
    row = TEXTS[key]
    return row[LANGUAGE_INDEX[language]]


# The language every later tr() call answers in.
_language = DEFAULT_LANGUAGE


def language():
    """Return the language this front-end currently speaks."""
    return _language


def set_language(code):
    """Select the language every later tr() call answers in."""
    global _language
    if code not in LANGUAGE_INDEX:
        raise ValueError("no such language: %s" % code)
    _language = code
    return _language


def tr(key):
    """Return one display string in the language this front-end speaks."""
    return TEXTS[key][LANGUAGE_INDEX[_language]]


def implicit_text():
    """Return the label every unused override control shows."""
    return tr("field.implicit")


# Where the chosen language is remembered between runs.
SETTINGS_APPLICATION = "minipro-gui"
SETTINGS_LANGUAGE_KEY = "language"


def read_language(settings=None):
    """Return the stored language, or the default when none was stored."""
    settings = settings or QtCore.QSettings(SETTINGS_APPLICATION,
                                            SETTINGS_APPLICATION)
    stored = settings.value(SETTINGS_LANGUAGE_KEY, DEFAULT_LANGUAGE)
    if not isinstance(stored, str) or stored not in LANGUAGE_INDEX:
        # A stored value this front-end cannot speak is not a language: fall
        # back instead of letting one bad setting make every label a raw key.
        return DEFAULT_LANGUAGE
    return stored


def store_language(code, settings=None):
    """Remember which language the next window should open in."""
    settings = settings or QtCore.QSettings(SETTINGS_APPLICATION,
                                             SETTINGS_APPLICATION)
    settings.setValue(SETTINGS_LANGUAGE_KEY, code)
    settings.sync()

# Database selectable with -q (main.c, case 'q').
DB_FILTERS = (
    ("tl866a", "TL866A/CS"),
    ("tl866ii", "TL866II+"),
    ("t48", "T48"),
    ("t56", "T56"),
    ("t76", "T76"),
)

# Memory regions accepted by -c (main.c, case 'c').  The second half of each
# row is a TEXTS key, so a window can be retitled without changing what -c
# receives.
MEMORY_VALUES = (
    (None, "memory.implicit"),
    ("code", "memory.code"),
    ("data", "memory.data"),
    ("config", "memory.config"),
    ("user", "memory.user"),
    ("calibration", "memory.calibration"),
)

# Configuration filters: long options only, no short letter (main.c).
CONFIG_SECTIONS = (
    (None, "config.all"),
    ("--fuses", "config.fuses"),
    ("--uid", "config.uid"),
    ("--lock", "config.lock"),
)

# File formats accepted by -f (main.c, case 'f').
FORMAT_VALUES = (
    (None, "format.auto"),
    ("ihex", "format.ihex"),
    ("srec", "format.srec"),
)

# -o ovc_level=N (main.c, parse_options): 0 is the firmware default and 1..9
# are the 120/150/180/200/220/250/300/350/400 mA trip levels (minipro.h).
OVC_MILLIAMPERES = (120, 150, 180, 200, 220, 250, 300, 350, 400)
OVC_LEVELS = ((0, None),) + tuple(
    (level, milli) for milli, level in zip(OVC_MILLIAMPERES, range(1, 10)))


def ovc_items():
    """Return the overcurrent choices: the firmware default plus each trip."""
    items = [(0, tr("ovc.implicit"))]
    for level, milli in OVC_LEVELS[1:]:
        items.append((level, tr("ovc.level") % milli))
    return items


def combo_items(table):
    """Translate one value/label-key table into value/label pairs."""
    return [(value, tr(key)) for value, key in table]


# Each combo is filled by one of these, so retranslating a window rebuilds the
# same choices with new words instead of retitling rows one by one.
def memory_items():
    return combo_items(MEMORY_VALUES)


def config_items():
    return combo_items(CONFIG_SECTIONS)


def format_items():
    return combo_items(FORMAT_VALUES)


def icsp_items():
    return combo_items(ICSP_VALUES)


def size_items():
    return combo_items(SIZE_MODES)


def autodetect_items():
    return [("8", tr("autodetect.spi8")), ("16", tr("autodetect.spi16"))]


def db_items():
    """The programmer names are the engine's own, so they are not translated."""
    return list(DB_FILTERS)


def fill_combo(combo, items):
    """Refill one combo, keeping the value the operator had chosen."""
    chosen = combo.currentData()
    combo.blockSignals(True)
    combo.clear()
    for value, label in items:
        combo.addItem(label, value)
    combo.blockSignals(False)
    index = combo.findData(chosen)
    if index >= 0:
        combo.setCurrentIndex(index)

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

# Text shown for each action in dialogs and in the journal.  The keys are
# TEXTS keys, so the word a sentence carries follows the window's language.
ACTION_LABELS = {
    "read": "word.read",
    "write": "word.write",
    "verify": "word.verify",
    "erase": "word.erase",
    "blank": "word.blank",
    "id": "word.id",
    "pin": "word.pin",
}

# Status and journal text for each action: the verb is stated only once.
ACTION_TITLES = {
    "read": "title.read",
    "write": "title.write",
    "verify": "title.verify",
    "erase": "title.erase",
    "blank": "title.blank",
    "id": "title.id",
    "pin": "title.pin",
}


def action_word(action):
    """Return the lower case name one action is called in a sentence."""
    return tr(ACTION_LABELS[action])


def action_title(action):
    """Return the title one action is reported under."""
    return tr(ACTION_TITLES[action])

# main.c rejects -x for these actions, so the GUI never sends it there.
SKIP_ID_ACTIONS = ("read", "verify", "blank", "pin")

# ICSP modes: -i supplies VCC, -I does not (main.c, case 'i' and 'I').
ICSP_VALUES = (
    (None, "icsp.standard"),
    ("-i", "icsp.with_vcc"),
    ("-I", "icsp.no_vcc"),
)

# File size handling: -s ignores the difference, while -S makes main.c treat a
# size difference as size_nowarn plus size_error, so it does not merely hide a
# warning: the operation continues with a file of a different size.
SIZE_MODES = (
    (None, "size.error"),
    ("-s", "size.ignore"),
    ("-S", "size.continue"),
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
            raise BinaryNotFound(tr("lookup.explicit") % explicit)
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
    raise BinaryNotFound(tr("lookup.missing"))



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
            self.log_line.emit(tr("runner.cancel"))
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
            self.log_line.emit(tr("runner.no_start"))
            self._finish(127)
        elif error == QtCore.QProcess.Timedout:
            self.log_line.emit(tr("runner.timeout"))

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
            self.log_line.emit(tr("runner.truncated") % self.captured_limit)
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
        # The language is chosen before the first label exists, so a window
        # never has to be built in one language and retitled afterwards.
        set_language(read_language())
        self.setWindowTitle(tr("window.title"))
        self.resize(1000, 760)
        self.runner = Runner()
        self.engine_version = ""
        self.engine_commit = ""
        self.engine_counts = {}
        self.programmer_found = None
        self.expect = None
        self.database_home = database_home(binary)

        central = QtWidgets.QWidget()
        self.main_layout = QtWidgets.QVBoxLayout(central)
        self.setCentralWidget(central)

        # Every control whose words come from the keyed table is recorded here,
        # so switching language retitles the window it built instead of building
        # a second one and hoping the operator notices which one to use.
        self._texts = []
        self._combos = []
        self._menus = []
        self._menu_actions = []
        self._override_combos = []

        self._build_banner()
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
            self._log(tr("journal.db_source") % self.database_home)
        self.update_file_info()
        self.refresh_programmer()

    # ------------------------------------------------------------- banner row
    def _build_banner(self):
        """Show, once and permanently, that this front-end is experimental.

        A modal warning at startup is dismissed and gone, so it teaches
        nothing about the operation the operator is about to run.  Keeping the
        warning in the window is what makes it part of the same view as the
        write and erase buttons, and keeping it enabled while an operation runs
        is what makes it readable during the one moment it matters.
        """
        banner = QtWidgets.QLabel(tr("banner.experimental"))
        banner.setObjectName("experimentalBanner")
        banner.setWordWrap(True)
        banner.setStyleSheet(
            "QLabel#experimentalBanner { color: #7a4a00; background: #fff4d6;"
            " padding: 6px; border: 1px solid #d9b36a; }")
        self.experimental_banner = banner
        self._texts.append((banner, "text", "banner.experimental"))
        self.main_layout.addWidget(banner)

    # ------------------------------------------------- retranslatable registry
    def _group_box(self, key):
        """Build one group box whose title can be retitled later."""
        box = QtWidgets.QGroupBox(tr(key))
        self._texts.append((box, "title", key))
        return box

    def _label(self, key):
        """Build one label whose text comes from the keyed table."""
        label = QtWidgets.QLabel(tr(key))
        self._texts.append((label, "text", key))
        return label

    def _button(self, key):
        """Build one button whose text comes from the keyed table."""
        button = QtWidgets.QPushButton(tr(key))
        self._texts.append((button, "text", key))
        return button

    def _check(self, key):
        """Build one check box whose text comes from the keyed table."""
        box = QtWidgets.QCheckBox(tr(key))
        self._texts.append((box, "text", key))
        return box

    def _tip(self, widget, key):
        """Give one control its tooltip through the keyed table."""
        self._texts.append((widget, "tip", key))
        widget.setToolTip(tr(key))
        return widget

    def _placeholder(self, widget, key):
        """Give one field its empty-state text through the keyed table."""
        self._texts.append((widget, "placeholder", key))
        widget.setPlaceholderText(tr(key))
        return widget

    def _combo(self, combo, items):
        """Fill one combo and remember how to refill it in another language."""
        fill_combo(combo, items())
        self._combos.append((combo, items))
        return combo

    # ------------------------------------------------------------------ header
    def _build_header(self):
        box = self._group_box("header.box")
        grid = QtWidgets.QGridLayout(box)
        grid.addWidget(self._label("header.programmer"), 0, 0)
        # The two answer labels show what the engine reported, not keyed text,
        # so they are retitled from what the window remembers rather than from
        # the registry: switching language must not erase an answer.
        self.programmer_label = QtWidgets.QLabel(tr("header.checking"))
        grid.addWidget(self.programmer_label, 0, 1)
        grid.addWidget(self._label("header.engine"), 1, 0)
        self.engine_label = QtWidgets.QLabel(tr("header.checking"))
        grid.addWidget(self.engine_label, 1, 1)
        self.programmer_button = self._button("header.reconnect")
        self.programmer_button.clicked.connect(self.refresh_programmer)
        grid.addWidget(self.programmer_button, 0, 2)
        self.engine_button = self._button("header.engine_details")
        self.engine_button.clicked.connect(self.show_engine_info)
        grid.addWidget(self.engine_button, 1, 2)
        grid.setColumnStretch(1, 1)
        self.main_layout.addWidget(box)

    # ------------------------------------------------------------ device group
    def _build_device_box(self):
        box = self._group_box("box.device")
        grid = QtWidgets.QGridLayout(box)

        grid.addWidget(self._label("device.database"), 0, 0)
        self.db_filter = self._combo(QtWidgets.QComboBox(), db_items)
        self._tip(self.db_filter, "box.device_tip")
        grid.addWidget(self.db_filter, 0, 1)
        self.db_count_label = QtWidgets.QLabel("")
        grid.addWidget(self.db_count_label, 0, 2)

        self.search = QtWidgets.QLineEdit()
        self._placeholder(self.search, "device.search_placeholder")
        self.search.returnPressed.connect(self.do_search)
        self.search_button = self._button("action.search")
        self.search_button.clicked.connect(self.do_search)
        grid.addWidget(self.search, 1, 0, 1, 2)
        grid.addWidget(self.search_button, 1, 2)

        grid.addWidget(self._label("box.device_device"), 2, 0)
        self.devices = QtWidgets.QComboBox()
        self.devices.setEditable(True)
        self.devices.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self._tip(self.devices, "box.device_name_tip")
        grid.addWidget(self.devices, 2, 1)
        self.info_button = self._button("box.info_button")
        self.info_button.clicked.connect(self.do_chip_info)
        grid.addWidget(self.info_button, 2, 2)

        self.autodetect_combo = self._combo(QtWidgets.QComboBox(),
                                             autodetect_items)
        self.autodetect_button = self._button("box.autodetect_button")
        self.autodetect_button.clicked.connect(self.do_autodetect)
        self.list_button = self._button("box.list_button")
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
        box = self._group_box("box.file")
        grid = QtWidgets.QGridLayout(box)

        grid.addWidget(self._label("file.path"), 0, 0)
        self.file = QtWidgets.QLineEdit()
        self.file.setText(os.path.join(os.getcwd(), "dump.bin"))
        self.file.textChanged.connect(self.update_file_info)
        self.open_button = self._button("file.open")
        self.open_button.clicked.connect(self.choose_input_file)
        self.save_button = self._button("file.save")
        self.save_button.clicked.connect(self.choose_output_file)
        grid.addWidget(self.file, 0, 1)
        grid.addWidget(self.open_button, 0, 2)
        grid.addWidget(self.save_button, 0, 3)

        grid.addWidget(self._label("file.memory"), 1, 0)
        self.memory = self._combo(QtWidgets.QComboBox(), memory_items)
        self._tip(self.memory, "file.memory_tip")
        grid.addWidget(self.memory, 1, 1)

        grid.addWidget(self._label("config.region"), 1, 2)
        self.cfg_section = self._combo(QtWidgets.QComboBox(), config_items)
        self._tip(self.cfg_section, "file.config_tip")
        grid.addWidget(self.cfg_section, 1, 3)

        grid.addWidget(self._label("file.format"), 2, 0)
        self.format = self._combo(QtWidgets.QComboBox(), format_items)
        self._tip(self.format, "file.format_tip")
        grid.addWidget(self.format, 2, 1)

        self.file_info = QtWidgets.QLabel("")
        grid.addWidget(self.file_info, 2, 2, 1, 2)
        grid.setColumnStretch(1, 1)
        self.main_layout.addWidget(box)

    # ---------------------------------------------------------- operations box
    def _build_operations_box(self):
        box = self._group_box("box.operations")
        grid = QtWidgets.QGridLayout(box)

        self.read_button = self._button("action.read")
        self.read_button.clicked.connect(self.do_read)
        self.verify_button = self._button("action.verify")
        self.verify_button.clicked.connect(self.do_verify)
        self.blank_button = self._button("action.blank")
        self.blank_button.clicked.connect(self.do_blank)
        self.id_button = self._button("action.id")
        self.id_button.clicked.connect(self.do_id)
        self.write_button = self._button("action.write")
        self.write_button.clicked.connect(self.do_write)
        self.erase_button = self._button("action.erase")
        self.erase_button.clicked.connect(self.do_erase)
        self.erase_button.setStyleSheet(
            "QPushButton { color: #a00000; font-weight: bold; }")
        self.pin_button = self._button("action.pin")
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
        box = self._group_box("box.advanced")
        box.setCheckable(True)
        box.setChecked(False)
        self.advanced = box
        grid = QtWidgets.QGridLayout(box)

        grid.addWidget(self._label("advanced.icsp"), 0, 0)
        self.icsp = self._combo(QtWidgets.QComboBox(), icsp_items)
        self._tip(self.icsp, "advanced.icsp_tip")
        grid.addWidget(self.icsp, 0, 1)

        grid.addWidget(self._label("advanced.vcc"), 0, 2)
        self.vcc = self._param_combo("advanced.vcc_tip")
        grid.addWidget(self.vcc, 0, 3)

        grid.addWidget(self._label("advanced.vpp"), 1, 0)
        self.vpp = self._param_combo("advanced.vpp_tip")
        grid.addWidget(self.vpp, 1, 1)

        grid.addWidget(self._label("advanced.vdd"), 1, 2)
        self.vdd = self._param_combo("advanced.vdd_tip")
        grid.addWidget(self.vdd, 1, 3)

        grid.addWidget(self._label("advanced.spi_clock"), 2, 0)
        self.spi_clock = self._param_combo("advanced.spi_clock_tip")
        grid.addWidget(self.spi_clock, 2, 1)

        grid.addWidget(self._label("advanced.pulse"), 2, 2)
        self.pulse = self._free_override("advanced.pulse_tip")
        grid.addWidget(self.pulse, 2, 3)

        grid.addWidget(self._label("advanced.i2c"), 3, 0)
        self.address = self._free_override("advanced.i2c_tip")
        grid.addWidget(self.address, 3, 1)

        grid.addWidget(self._label("advanced.ovc"), 3, 2)
        self.ovc = self._combo(QtWidgets.QComboBox(), ovc_items)
        self._tip(self.ovc, "advanced.ovc_tip")
        grid.addWidget(self.ovc, 3, 3)

        grid.addWidget(self._label("advanced.file_size"), 4, 0)
        self.size_mode = self._combo(QtWidgets.QComboBox(), size_items)
        self._tip(self.size_mode, "advanced.file_size_tip")
        grid.addWidget(self.size_mode, 4, 1)

        self.skip_erase = self._check("advanced.skip_erase")
        self._tip(self.skip_erase, "advanced.skip_erase_tip")
        self.skip_verify = self._check("advanced.skip_verify")
        self._tip(self.skip_verify, "advanced.skip_verify_tip")
        self.skip_blank = self._check("advanced.skip_blank")
        self._tip(self.skip_blank, "advanced.skip_blank_tip")
        self.unprotect = self._check("advanced.unprotect")
        self._tip(self.unprotect, "advanced.unprotect_tip")

        grid.addWidget(self.skip_erase, 5, 0)
        grid.addWidget(self.skip_verify, 5, 1)
        grid.addWidget(self.skip_blank, 5, 2)
        grid.addWidget(self.unprotect, 5, 3)

        self.skip_id = self._check("advanced.skip_id")
        self._tip(self.skip_id, "advanced.skip_id_tip")
        self.id_error = self._check("advanced.id_error")
        self._tip(self.id_error, "advanced.id_error_tip")
        self.protect = self._check("advanced.protect")
        self._tip(self.protect, "advanced.protect_tip")
        grid.addWidget(self.skip_id, 6, 0)
        grid.addWidget(self.id_error, 6, 1)
        grid.addWidget(self.protect, 6, 3)

        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        self.main_layout.addWidget(box)

    def _param_combo(self, tip_key):
        """Build one editable override combo filled from the chip database."""
        combo = QtWidgets.QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        combo.addItem(implicit_text())
        self._override_combos.append(combo)
        self._tip(combo, tip_key)
        return combo

    def _free_override(self, tip_key):
        """Build one free text field that stays empty when it is unused."""
        field = QtWidgets.QLineEdit()
        self._placeholder(field, "field.implicit")
        self._tip(field, tip_key)
        return field

    # -------------------------------------------------------------- log area
    def _build_log_area(self):
        box = self._group_box("box.log")
        layout = QtWidgets.QVBoxLayout(box)
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.log.setMaximumBlockCount(5000)
        self._placeholder(self.log, "log.placeholder")
        layout.addWidget(self.log)
        self.main_layout.addWidget(box, 1)

    # ------------------------------------------------------------- status row
    def _build_status_row(self):
        self.status_row = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(self.status_row)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.status = self._label("status.ready")
        self.cancel = self._button("action.cancel")
        self.cancel.clicked.connect(self.do_cancel)
        self.cancel.setEnabled(False)
        layout.addWidget(self.progress, 2)
        layout.addWidget(self.status, 2)
        layout.addWidget(self.cancel)
        self.main_layout.addWidget(self.status_row)

    # ------------------------------------------------------------------ menus
    def _menu(self, bar, key):
        """Add one menu whose title can be rebuilt in another language."""
        menu = bar.addMenu(tr(key))
        self._menus.append((menu, key))
        return menu

    def _action(self, menu, key, slot):
        """Add one menu entry whose text can be rebuilt in another language."""
        action = menu.addAction(tr(key), slot)
        self._menu_actions.append((action, key))
        return action

    def _build_menus(self):
        """Build every menu, so switching language can rebuild them cleanly."""
        bar = self.menuBar()
        # A retranslate rebuilds the menus instead of adding a second set on
        # top of the first, so every recorded menu is dropped first.
        for menu, _key in self._menus:
            bar.removeAction(menu.menuAction())
        del self._menus[:]
        del self._menu_actions[:]

        self.file_menu = self._menu(bar, "menu.file")
        self._action(self.file_menu, "menu.choose_file", self.choose_input_file)
        self._action(self.file_menu, "menu.save_as", self.choose_output_file)
        self.file_menu.addSeparator()
        self._action(self.file_menu, "menu.quit", self.close)

        self.operations_menu = self._menu(bar, "menu.operations")
        self._action(self.operations_menu, "action.read", self.do_read)
        self._action(self.operations_menu, "action.verify", self.do_verify)
        self._action(self.operations_menu, "action.blank", self.do_blank)
        self._action(self.operations_menu, "action.id", self.do_id)
        self._action(self.operations_menu, "action.write", self.do_write)
        self._action(self.operations_menu, "action.erase", self.do_erase)
        self._action(self.operations_menu, "action.pin", self.do_pin_check)
        self.operations_menu.addSeparator()
        self._action(self.operations_menu, "action.hardware",
                     self.do_hardware_check)

        self.db_menu = self._menu(bar, "menu.database")
        self._action(self.db_menu, "menu.list_chips", self.do_list_devices)
        self._action(self.db_menu, "menu.search_chip", self.do_search)
        self._action(self.db_menu, "menu.chip_info", self.do_chip_info)
        self._action(self.db_menu, "menu.autodetect", self.do_autodetect)
        self.db_menu.addSeparator()
        self._action(self.db_menu, "menu.supported_programmers",
                     self.do_supported_programmers)

        self.view_menu = self._menu(bar, "menu.view")
        self.language_menu = self.view_menu.addMenu(tr("menu.language"))
        group = QtWidgets.QActionGroup(self)
        group.setExclusive(True)
        self.language_actions = {}
        for code, name in LANGUAGE_CHOICES:
            action = self.language_menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(language() == code)
            action.triggered.connect(
                lambda checked, code=code: self.choose_language(code))
            group.addAction(action)
            self.language_actions[code] = action

        self.help_menu = self._menu(bar, "menu.help")
        self._action(self.help_menu, "menu.about", self.show_about)

    def choose_language(self, code):
        """Speak another language, and remember it for the next window."""
        if code == language():
            return False
        set_language(code)
        store_language(code)
        self.retranslate_ui()
        return True

    def retranslate_ui(self):
        """Retitle every control this window built, without rebuilding it."""
        self.setWindowTitle(tr("window.title"))
        for widget, kind, key in self._texts:
            text = tr(key)
            if kind == "title":
                widget.setTitle(text)
            elif kind == "tip":
                widget.setToolTip(text)
            elif kind == "placeholder":
                widget.setPlaceholderText(text)
            else:
                widget.setText(text)
        # The two header labels carry engine answers, not keyed text, so they
        # only show the waiting text while nothing has been asked yet.
        if not self.engine_version:
            self.engine_label.setText(tr("header.checking"))
        if self.programmer_found:
            self.programmer_label.setText(self.programmer_found)
        elif self.programmer_found is None:
            self.programmer_label.setText(tr("header.checking"))
        else:
            self.programmer_label.setText(tr("header.no_programmer"))
        if not self.runner.running():
            self.status.setText(tr("status.ready"))
        for combo, items in self._combos:
            fill_combo(combo, items())
        # The override combos keep the values the engine reported for the
        # selected chip, so only their waiting text is retitled.  An editable
        # combo shows its current item in the text field, so a value the
        # operator typed has to be put back after that item is retitled.
        for combo in self._override_combos:
            if not combo.count():
                continue
            typed = combo.currentText()
            was_waiting = typed == combo.itemText(0)
            combo.setItemText(0, implicit_text())
            if not was_waiting:
                combo.setEditText(typed)
        for code, action in self.language_actions.items():
            action.setChecked(language() == code)
        self.update_file_info()
        self._build_menus()

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
            return self._refuse(tr("refusal.device.title"),
                                tr("refusal.device.body"))
        return device

    def _require_file(self, output=False):
        """Refuse a file operation when the dump path is empty or unusable.

        output=True marks the path as something the engine will write: minipro
        opens a read target with "wb", which truncates whatever is already
        there, so an existing file needs an explicit confirmation first.
        """
        path = self.file.text().strip()
        if not path:
            return self._refuse(tr("refusal.file.title"),
                                tr("refusal.file.body"))
        if os.path.isdir(path):
            return self._refuse(tr("refusal.unreadable.title"),
                                tr("refusal.directory.body") % path)
        if output:
            if os.path.exists(path) and not os.access(path, os.W_OK):
                return self._refuse(tr("refusal.unwritable.title"),
                                    tr("refusal.unwritable.body") % path)
            if os.path.exists(path) and not self._confirm_dump_overwrite(path):
                return None
        return path

    def _require_readable_file(self):
        """Refuse an action whose dump the engine must read from disk."""
        path = self._require_file()
        if not path:
            return None
        if not os.path.isfile(path):
            return self._refuse(tr("refusal.file.title"),
                                tr("refusal.missing.body") % path)
        if not os.access(path, os.R_OK):
            return self._refuse(tr("refusal.unwritable.title"),
                                tr("refusal.unreadable.body") % path)
        return path

    def _confirm_dump_overwrite(self, path):
        """Ask before a read replaces a dump that already exists."""
        warning = tr("confirm.overwrite.body") % (path, os.path.getsize(path))
        self._log(warning)
        answer = QtWidgets.QMessageBox.question(
            self, tr("confirm.overwrite.title"),
            warning + "\n" + tr("confirm.overwrite.more"),
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
                self._log(tr("journal.config_forces"))
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
                    self._log(tr("journal.skip_id") % ACTION_LABELS[action])
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
            if value and value != implicit_text():
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
                self, tr("refusal.running.title"),
                tr("refusal.running.body"))
            return False
        self.expect = (action, label)
        self._set_busy(True)
        self.status.setText(label)
        if not self.runner.start(self.binary, args, label, self.database_home):
            self.expect = None
            self._set_busy(False)
            self.status.setText(tr("status.ready"))
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
        if not self._confirm(tr("confirm.hardware.title"),
                             tr("confirm.hardware.body")):
            return
        self._start(["-t"], "hardware", tr("task.hardware"))

    def _confirm_write(self, path):
        """Ask once before a write, naming the options the engine will get."""
        text = tr("confirm.write.body") % (path, self.selected_device(),
                                           self._override_summary("write"))
        # The operator may have asked for an option main.c drops for a write,
        # so the dialog has to say that instead of leaving it unmentioned.
        dropped = self._dropped_options("write")
        if dropped:
            text += "\n\n" + "\n".join(dropped)
        answer = QtWidgets.QMessageBox.question(
            self, tr("confirm.write.title"),
            text + tr("confirm.continue"),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No)
        return answer == QtWidgets.QMessageBox.Yes

    def _override_summary(self, action):
        """Name every option this command line really sends for one action."""
        parts = self._sent_options(action)
        if not parts:
            if self.advanced.isChecked():
                return tr("confirm.options_advanced_none")
            return tr("confirm.options_none")
        return tr("confirm.options_sent") + ", ".join(parts)

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
            if value and value != implicit_text():
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
            dropped.append(tr("confirm.options_dropped"))
        return dropped

    def _confirm_erase(self):
        """Ask twice before an erase, naming the options it really sends.

        main.c never reads the chosen region when it erases: the ERASE action
        calls erase_device(), and that never reads the chosen page.  So the
        region shown here must not read as something that limits the erase.
        """
        warning = tr("confirm.erase.body") % (
            self.selected_device(), self._override_summary("erase"))
        self._log(warning)
        first = QtWidgets.QMessageBox.warning(
            self, tr("confirm.erase.title"),
            warning + tr("confirm.continue"),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No)
        if first != QtWidgets.QMessageBox.Yes:
            return False
        second = QtWidgets.QMessageBox.warning(
            self, tr("confirm.final.title"),
            tr("confirm.final.body"),
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
        self._start(self._db_args(["-l"]), "list", tr("task.list"))

    def do_search(self):
        """Search the selected database for one chip name."""
        term = self.search.text().strip()
        if not term:
            QtWidgets.QMessageBox.information(self, tr("search.title"),
                                              tr("search.empty"))
            return
        self._start(self._db_args(["-L", term]), "search",
                    tr("task.search") % term)

    def do_chip_info(self):
        """Show the database record of the selected chip."""
        device = self._require_device()
        if not device:
            return
        self._start(self._db_args(["-d", device]), "info",
                    tr("task.chip_info") % device)

    def do_autodetect(self):
        """Autodetect an SPI chip in the selected package."""
        if not self._confirm(tr("confirm.autodetect.title"),
                             tr("confirm.autodetect.body")):
            return
        self._start(["-a", self.autodetect_combo.currentData()], "autodetect",
                    tr("task.autodetect"))

    def do_supported_programmers(self):
        """List the programmer families the engine claims to support."""
        self._start(["-Q"], "supported", tr("task.supported"))

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
        action, label = self.expect or ("none", tr("title.operation"))
        self.expect = None
        self._set_busy(False)
        if self.runner.cancelled:
            self.status.setText(tr("status.cancelled"))
            self._log(tr("journal.cancelled") % (label, exit_code))
            return
        if exit_code == 0:
            self.status.setText(tr("status.done") % label)
            self._log(tr("journal.done") % label)
        else:
            self.status.setText(tr("status.failed") % (label, exit_code))
            self._log(tr("journal.failed") % (label, exit_code))
        if exit_code != 0:
            # A failed call produced no listing and no chip record, so anything
            # still on screen comes from an earlier call that did succeed.
            if action in ("list", "search", "autodetect", "info", "version",
                          "presence"):
                self._log(tr("journal.stale_views"))
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
            self._log(tr("journal.no_chips"))
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
        self._log(tr("journal.device_count") % len(names))

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
            self._reset_chip_overrides(tr("journal.reset_chip"))
        elif self.chip_overrides_from != self.db_filter.currentData():
            self._reset_chip_overrides(tr("journal.reset_database"))

    def _reset_chip_overrides(self, reason):
        """Return every chip-specific override to the database default."""
        for combo in (self.vcc, self.vdd, self.vpp, self.spi_clock):
            self._fill_combo(combo, ())
        self.chip_overrides_for = None
        self.chip_overrides_from = None
        self._log(tr("journal.overrides_reset") % reason)

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
        self.engine_label.setText(
            tr("engine.commit") % (self.engine_version or "?",
                                   self.engine_commit or "?"))
        counts = ", ".join("%s: %d" % (key, value)
                           for key, value in sorted(self.engine_counts.items()))
        self.db_count_label.setText(counts)

    def apply_programmer(self, text):
        """Show which programmer -k found, or say that none is connected."""
        found = parse_programmer(text)
        # Remembered so switching language retitles the answer instead of
        # dropping the window back to its waiting text.  An empty name means
        # the engine was asked and found nothing, which is not the same as
        # never having asked.
        self.programmer_found = found or ""
        if found:
            self.programmer_label.setText(found)
        else:
            self.programmer_label.setText(tr("header.no_programmer"))

    def refresh_programmer(self):
        """Ask the engine which programmer is connected right now."""
        self._start(["-k"], "presence", tr("task.presence"))

    def show_engine_info(self):
        """Ask the engine for its version, commit and database sizes."""
        self._start(["-V"], "version", tr("task.version"))

    def show_about(self):
        """Explain what this front-end does and what it does not do."""
        QtWidgets.QMessageBox.information(
            self, tr("about.title"),
            tr("about.body") % (
                tr("about.experimental"),
                tr("about.engine") % (self.binary,
                                      self.engine_version or "?",
                                      self.engine_commit or "?")))


    # ------------------------------------------------------------ file dialog
    def choose_input_file(self):
        """Choose the dump file through the native open dialog."""
        path = QtWidgets.QFileDialog.getOpenFileName(
            self, tr("file_dialog.input_title"),
            os.path.dirname(self.file_path()) or ".",
            tr("file_dialog.input_filters"))
        path = path[0] if isinstance(path, tuple) else path
        if path:
            self.file.setText(path)
            self.update_file_info()

    def choose_output_file(self):
        """Set the dump path through the native save dialog."""
        path = QtWidgets.QFileDialog.getSaveFileName(
            self, tr("file_dialog.output_title"), self.file_path(),
            tr("file_dialog.output_filters"))
        path = path[0] if isinstance(path, tuple) else path
        if path:
            self.file.setText(path)
            self.update_file_info()

    def update_file_info(self):
        """Report the size of the chosen dump file, or why it is unusable."""
        path = self.file_path()
        if not path:
            self.file_info.setText(tr("file.none"))
            return
        if os.path.isdir(path):
            self.file_info.setText(tr("file.is_dir"))
        elif os.path.isfile(path):
            self.file_info.setText(tr("file.size") % os.path.getsize(path))
        else:
            self.file_info.setText(tr("file.missing"))

    # ------------------------------------------------------------- cancel/close
    def do_cancel(self):
        """Ask the engine to stop the operation that is running."""
        if not self.runner.cancel():
            self.status.setText(tr("status.no_operation"))

    def closeEvent(self, event):
        """Stop the engine, and only close once it really stopped."""
        if self.runner.running():
            answer = QtWidgets.QMessageBox.question(
                self, tr("close.title"), tr("close.body"),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No)
            if answer != QtWidgets.QMessageBox.Yes:
                event.ignore()
                return
            self.runner.cancel()
            if not self._await_process_exit():
                QtWidgets.QMessageBox.critical(
                    self, tr("close.refused_title"),
                    tr("close.refused_body"))
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
        description=tr("main.description"))
    parser.add_argument("--binary", help=tr("main.binary_help"))
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
