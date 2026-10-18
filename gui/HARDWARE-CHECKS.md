# Hardware checks for the front-end

This file is the gate the README describes: nothing from `gui/` is offered to
the upstream project until the front-end has been exercised on real hardware,
programmer by programmer and chip by chip.  It lives in `gui/` because `gui/`
is exactly what that gate holds back, so a change to the front-end carries its
checklist with it and the upstream documentation in `doc/` stays upstream's.

Nothing here is filled in.  No programmer and no chip has been touched by this
front-end: every check that runs today runs against sealed stand-ins that
behave like `minipro`.  What follows is the procedure and the blanks, not
results.


## A. What this sheet is, and what "done" means

### The rule that makes a row mean something

The window does one thing: it builds a command line, runs the engine, and
shows what the engine printed.  A row therefore only counts when its result is
compared with something the window did not produce:

  * the same command typed by hand in a terminal, on the same programmer and
    the same chip;
  * a dump whose content is known from outside this program;
  * the chip's own state, read back by a different action.

Never compare the window with the window.  A session that records only that
the window said OK makes the window its own oracle: it cannot tell a correct
read from a read that returned the wrong bytes, because both end with the same
journal line.  The engine is the same program on both sides of that
comparison, so the engine's verdict proves nothing about the window.  What is
under test is whether the window asked for the right thing, and whether it
reported the answer honestly.

### The journal is the evidence

The first line every operation writes to the journal is the exact command the
window ran, quoted the way a shell would quote it.  That line, not a
recollection of which button was pressed, is what a row compares against the
hand-typed command.  The path in front of it depends on where the window found
the engine, so compare everything after the first word.

### Non-destructive and destructive

| Kind | Options | What they may touch |
| --- | --- | --- |
| Non-destructive | `-k` `-V` `-Q` `-t` `-a` `-l` `-L` `-d` `-r` `-m` `-b` `-D` `-z` | the chip is only read; a dump file may be written |
| Destructive | `-w` `-E` | the chip's contents are replaced or destroyed |

A read replaces the dump file named in the file field, and the window asks
before replacing a file that already exists; it never changes the chip.  A
write or an erase changes the chip, so both run on a sacrificial part only,
after its contents have been read out and put somewhere else.  Back up before
every write and every erase, even when the part is believed to be empty.

### Definition of done

The gate lifts when all four of these hold, and not before:

1. Every row of the matrix in section D is filled in for every programmer on
   the bench: PASS, or FAIL with the fix and the re-run recorded in the
   session log, or N/A with a reason.  A reason for an N/A row is copied into
   the limitations paragraph of the README, so a reader of the front-end sees
   what was never tried.  No cell is left blank.
2. The two front-end-only invariants pass on every programmer on the bench:
   C.13, that an override chosen in the window reaches the command line, and
   C.16, that a cancelled operation leaves the programmer answering the next
   command.
3. The session names the engine commit and the front-end commit it was run
   against, and the front-end commit is the one at `origin/master` at that
   moment.
4. `make gui-test` is green on that same commit.

Only then is the gate sentence in the README removed and an upstream proposal
drafted.  A partially filled sheet is not a smaller gate: it is a sheet that
has not been finished.

### Branch rule

Work lands on `master` of this fork.  A commit that changes the command-line
surface - `_op_args`, `_add_overrides`, `ACTION_FLAGS`, the option value
tables, or the parsing of engine output - gets its hardware rows run before
another commit lands on top of it, so a FAIL stays bisectable.  Nothing lands
on `master` with a red `make gui-test`.

### What this sheet cannot prove

It proves that on the parts and programmers on the bench, the window asked for
what the controls said and reported what the engine answered.  It does not
prove the engine's own algorithms, and it says nothing about a chip or a
programmer that was not on the bench.  A PASS row is a statement about one
part in one socket on one host, which is why the session log records all three.



## B. What the window can put on a command line

Every option the front-end is able to send, and the row that exercises it.  A
sealed check in `gui/tests/minipro_regression.py` fails when the window learns
an option that this table does not name, so a new control cannot slip past the
gate unnoticed.

| Option | Where it comes from | Row |
| --- | --- | --- |
| `-r` `-w` `-m` `-E` `-b` `-D` `-z` | the action buttons and the Operations menu | C.7, C.8, C.9, C.10, C.11, C.19 |
| `-k` | the Reconnect button, and the probe the window makes at startup | C.1, C.2 |
| `-V` | the Engine details button | C.3 |
| `-Q` | Database -> Supported programmers | C.4 |
| `-t` | Operations -> Hardware check | C.5 |
| `-d` `-l` `-L` `-q` | the chip record, list and search controls | C.6, C.14 |
| `-a` | the autodetect combo and button | C.12 |
| `-p` | the chip name field, on every programming action | every row that programs |
| `-c` | the memory region combo | C.14 |
| `--fuses` `--uid` `--lock` | the config filter combo, which forces `-c config` | C.14 |
| `-f` | the file format combo | C.14 |
| `-i` `-I` | the ICSP combo in the advanced group | C.15 |
| `-s` `-S` | the file size combo in the advanced group | C.15 |
| `-x` `-e` `-v` `-u` `-P` `-B` `-y` | the check boxes in the advanced group | C.15 |
| `--vcc` `--vdd` `--vpp` `--spi_clock` | the four override combos, filled from the chip record | C.13 |
| `--pulse` `--address` | the two free override fields | C.13 |
| `-o` | the overcurrent combo, as `-o ovc_level=N` | C.13 |

The window sends `-q` with a database query only, never with a programming
action, and it never sends `-x` for an action the engine refuses it for; both
rules come from `src/main.c` and both are what the rows below check.


## C. The procedure

One block per check.  Each names what to do, the journal line the window must
have written first, what the outcome has to be, and what goes in the log.  The
journal lines quoted here are the front-end's own words, in English; run the
session in English and re-run C.20 in Romanian.  Anything the engine prints is
recorded verbatim, never paraphrased and never written down before it is seen.

Before the session: record the host, the engine build (`git rev-parse HEAD` in
the engine tree, and what `-V` reports), the front-end commit, and the Qt and
PyQt versions.  Fill in the bench table in section D first: it decides which
rows exist at all.

### C.1 Start with no programmer attached

Do: leave the programmer unplugged, and start the window.

Journal: the window writes `The databases are read from <directory>.` when it
found a database home, then the command of the probe it makes on its own:
`<engine> -k`.

Expect: the window opens and stays responsive; the banner stays visible; the
programmer field reads `no programmer found`; the probe ends with
`Programmer detection failed, exit code <n>.`.  The window must not sit
waiting for a keyboard: it gives the engine its input from an empty device, so
an engine that would have asked a question on the terminal fails instead of
pausing forever.

Record: the exit code, whether anything was asked for, whether the window
stayed responsive throughout.

### C.2 Attach the programmer and reconnect

Do: plug the programmer in, wait for the host to enumerate it, press
Reconnect.

Journal first line: `<engine> -k`

Expect: `Programmer detection finished successfully.` and the programmer field
showing the model the engine named.

Record: that model string verbatim, and the firmware the engine reports for it
if it reports one.  This is the row that decides whether the rest of the
column exists.

### C.3 Engine details

Do: press Engine details.

Journal first line: `<engine> -V`

Expect: `Engine information finished successfully.` and the engine field
showing a version and a commit.

Record: both, and check the commit against `git rev-parse HEAD` in the engine
tree.  A mismatch means the window is driving a different engine than the one
that was built, which invalidates every other row of the session.

### C.4 The programmers the engine claims

Do: Database -> Supported programmers.

Journal first line: `<engine> -Q`

Expect: `Supported programmers finished successfully.` and a list in the
views.  The list is the engine's claim, not this window's.

Record: whether the programmer on the bench appears in it.

### C.5 The programmer's own self test

Do: Operations -> Hardware check.  This is a menu action: there is no button
for it.  Answer the confirmation `The hardware test uses the connected
programmer.` with Yes.

Journal first line: `<engine> -t`  Note that no `-p` is on it: the hardware
test is not about a chip, and the window does not invent a chip for it.

Expect: `Hardware test finished successfully.` on a healthy programmer, and a
failure line when the programmer is absent or busy.  The buttons lock while it
runs and come back when it ends.

Record: the engine's own verdict verbatim, and whether the controls came back.

### C.6 The chip record

Do: type the chip name into the chip field, then Database -> Chip information.

Journal first line: `<engine> -q <database> -d <chip>`

Expect: `Information <chip> finished successfully.` and the record in the
views.  The four override combos - `--vcc`, `--vdd`, `--vpp`, `--spi_clock` -
are refilled from the record's own "Available ..." headers.

Record: the chip name as the engine spells it, the four lists as the record
gives them, and whether the combos show exactly those values.  A combo that
offers a voltage the record does not list is a control that lies about the
chip.

### C.7 Read, twice

Do: name a dump file that does not exist yet and press Read.  Then name a
second file and read again.

Journal first line: `<engine> -p <chip> -r <dump>`.  With every selector left
at its implicit value that is the whole command; anything else on the line is
something the window added that no control was asking for.

Compare: type the same command by hand in a terminal.  The two command lines
must be identical after the engine path.

Expect: `Read finished successfully.`, a file whose size matches what the file
information line shows, and a second file of the same size.

Against something outside the window: both dumps must be byte-identical to a
reference taken elsewhere - another programmer, an earlier session, a known
pattern.  Two dumps that match each other prove only that the same mistake was
made twice.

Record: the SHA-256 of the reference, of the first dump and of the second.
Then read over a file that already exists: the window must ask first, naming
the file and its size, and must not touch it until the answer is Yes.

### C.8 Verify, and verify against a broken copy

Do: press Verify with the good dump in the file field.  Then copy the dump,
flip one byte in the copy, point the file field at the copy, and press Verify
again.

Journal first line: `<engine> -p <chip> -m <dump>`, twice.

Expect: `Verify finished successfully.` the first time; `Verify failed, exit
code <n>.` and `Verify failed (code <n>).` the second.  A verify that passes
against a file that differs from the chip is a verify that never compared
anything, and it is the single most dangerous thing this window could do
quietly.

Record: the offset of the flipped byte, and the exit code of the failing
verify.

### C.9 Blank check, both ways

Do: run the blank check on a part that is erased, and again on a part that
holds data.

Journal first line: `<engine> -p <chip> -b`.  The blank check takes no file,
and the window must not hand it one.

Expect: `Blank check finished successfully.` on the erased part, and a failure
line on the part that holds data.

Record: which part was in the socket for each run, and the engine's verdict
verbatim for both.

### C.10 ID read

Do: press the ID action.

Journal first line: `<engine> -p <chip> -D`

Expect: `ID read finished successfully.` and an answer in the views.

Record: the ID the engine reported, and the ID this part is expected to have.
An answer of all zero or all FF is not an answer.

### C.11 Pin check, clean and broken

Do: run the pin check with the part seated properly.  Then lift one pin out of
contact, or offset the part in the socket, and run it again.

Journal first line: `<engine> -p <chip> -z`

Expect: a pass the first time and a failure the second.  The second half is
the point: a pin check that passes with a pin deliberately out of contact
checked nothing at all.

Record: the pin the engine named, and the pin that was actually broken.  They
have to be the same pin.

### C.12 SPI autodetect, filled and empty

Do: pick the package in the autodetect combo and press Autodetect with a known
SPI part in the socket; answer `Autodetection reads the chip sitting in the
socket.` with Yes.  Then repeat with the socket empty.

Journal first line: `<engine> -a <8|16>`.  No `-p` is on it: finding out what
`-p` should be is the whole operation.

Expect: with a part, `Autodetect SPI finished successfully.` and a name; with
an empty socket, a failure line and no name offered.  An autodetect that names
a chip for an empty socket invents hardware.

Record: the name returned, and the part that was really in the socket.

### C.13 Overrides reach the command line

Do: open the Advanced group first.  It starts closed, its controls are
disabled until it is open, and while it is closed the window sends none of
these options, whatever the combos show.  With the record from C.6 loaded,
choose a `--vcc` and a `--spi_clock` that are not the defaults, type a
`--pulse` and an `--address`, and set the overcurrent combo away from the
firmware default.  Then run a non-destructive action, a read will do.

Journal first line: the same command as C.7 plus `--vcc <value>`,
`--spi_clock <value>`, `--pulse <value>`, `--address <value>` and
`-o ovc_level=<n>`.  An option that was set in a control and is missing from
this line was never sent: the control moved, the engine never heard about it.

Compare: the same command typed by hand with the same options.

Expect: the action still ends with its own verdict.  The options changed the
run; they did not break how the command is read.  Then close the box and run
the same action again: `--vcc`, `--spi_clock`, `--pulse`, `--address` and `-o`
must all be absent from that line.  A closed box is the window saying it is
not applying them.

Then change the chip name, or the database combo.  The journal must write `The
chip overrides were reset: the selected chip changed.` or `The chip overrides
were reset: the selected database changed.`, and the four combos must return
to implicit.  An override that survives a change of chip is an override about
to be applied to a chip that never asked for it.

Record: the full command line, the reset line, and the values each combo held
before and after.

### C.14 The region, the config filter and the format

Do: Database -> List chips, then Database -> Search for a name that exists and
one that does not.  Then set the memory region combo to a region this chip
really has and read; then set the config filter combo to one of its entries
and read again; then set the file format combo to a format other than the
automatic one and read.

Journal first lines: `<engine> -q <database> -l`, `<engine> -q <database> -L
<term>`, `<engine> -p <chip> -c <region> -r <dump>`, `<engine> -p <chip> -c
config --fuses -r <dump>`, `<engine> -p <chip> -f <format> -r <dump>`.  When a
config filter is chosen the journal also says `The config filter forces -c
config.`, because the filter is meaningless without the region the window then
insists on.

Expect: a search for a name that is not in the database ends with `No chip
found in the selected database.` and not with a list of near misses.  A region
read must be the size the record gives for that region, and two different
regions must not produce the same bytes.  A file written in a chosen format
must be readable as that format.

N/A is allowed for a region or a filter this chip does not have; the reason
goes in the log and in the README limitations.

Record: the sizes read for each region, the forced-config line, and what the
file looked like in the chosen format.

### C.15 The advanced options, one at a time

Do: with the Advanced group open and a non-destructive action queued, set one
advanced control at a time and run it, leaving every other advanced control at
rest and the overrides from C.13 reset.  The check boxes are `-x`, `-e`,
`-v`, `-u`, `-P`, `-B`, `-y`; the ICSP combo sends `-i` or `-I`; the file size
combo sends `-s` or `-S`.

Expect: the journal command line carries exactly the option belonging to the
control that was moved, and nothing else.  `-x` is refused for a read, a
verify, a blank check and a pin check: the journal must say `-x is not allowed
for <action>; ignoring it.` and the command must not carry it.  Where an option
cannot be observed on this chip, mark the row N/A with the reason.

Record: one command line per control.  A control whose option never reaches the
command line is a control that does nothing, and it will keep looking useful.

### C.16 Cancel, and what the programmer is left as

Do: start a read of a part large enough to take a few seconds, and press
Cancel part way through.

Journal: `Read cancelled (code <n>).`

Expect: the buttons come back, the progress bar stops, and the partial file is
left where it is - the window does not delete what the engine half wrote.  Then
press the ID action: the programmer must answer it.  A programmer left halfway
through a transaction by a cancelled operation will mis-report everything that
comes after it, which is the part of this row that matters.

Record: the exit code, whether the file was left alone, and whether the ID read
afterwards succeeded.

### C.17 A second operation while one runs

Do: start a read, and while it runs try the Operations menu actions.  They are
not greyed out during a run; the buttons are.

Expect: the menu action produces the box `Process running` - `Wait for the
current operation to end, or cancel it.` - and no second engine process starts.

Record: how many engine processes existed during the run, from the process
table and not from the window.  Two at once on one USB programmer is a defect
even if the window looks calm.

### C.18 Close while an operation runs

Do: start an operation and press the window's close button.

Expect: exactly one box, `Quit` - `A minipro operation is still running. Cancel
it and quit?`.  Yes stops the engine and quits; No leaves the window open and
the operation running.  If the engine refuses to stop, the window must say so
with `Quit refused` and stay open rather than disappear and leave a process
behind it.

Record: which answer was given, whether an engine process was still running
afterwards, and whether the chip was left in the middle of an operation.

### C.19 The write path, sacrificial part only

A part that is worth keeping must not be in the socket for this row.  Read the
part out and put the dump somewhere safe before anything in this row runs.

Do, in this order: back up with a read; write a known pattern; verify against
it; read the part back into a second file; erase; run the blank check; read
again.

Journal first lines, in order: `-p <chip> -r <backup>`, `-p <chip> -w
<pattern>`, `-p <chip> -m <pattern>`, `-p <chip> -r <after>`, `-p <chip> -E`,
`-p <chip> -b`, `-p <chip> -r <erased>`.

Confirmations: a write asks once, and the text names the options it really
sends; an erase asks twice, and its first warning says plainly that the region
chosen does not limit the erase.  If either asks a different number of times,
or asks without naming what it is about to do, that is a finding.

Expect: the verify passes; the read-back is byte-identical to the pattern that
was written; the blank check passes after the erase; the last read is all FF,
or all 00 for a part that erases to zero - record which this one does.

Record: the SHA-256 of the pattern and of the read-back, the content of the
final read, and the exit code of the erase.

### C.20 The window in the other language, mid-session

Do: switch the language while the session is running, then repeat one
non-destructive action.

Expect: the controls retitle and the chosen values stay; the command line does
not change, because the options are the engine's words and not the window's;
the journal sentences come in the other language.

Record: the command line before and after the switch.  They must be identical
apart from the engine path.


## D. The bench, and the matrix

### What is on the bench

The five names below are the programmer databases the window can query, taken
from the engine's own list.  They are candidates: nothing here says that any of
them is owned, plugged in or working.  Fill this table in first, because it
decides which columns exist below.  A programmer marked n has no column, and
that absence is a limitation the README has to state - it is not a pass.

| Programmer | On the bench? (y/n) | Model as `-k` reports it | Firmware |
| --- | --- | --- | --- |
| TL866A/CS | | | |
| TL866II+ | | | |
| T48 | | | |
| T56 | | | |
| T76 | | | |

### The parts

| Role | Part | How its content is known |
| --- | --- | --- |
| Reference part: read, verified, ID, pin check (C.7 to C.11) | | |
| Sacrificial part: written and erased (C.19) | | |

### The matrix

One cell per check per programmer.  C.1 and C.20 belong to the host and the
window rather than to a programmer: fill them once per session and carry the
same verdict across the row.

| Check | TL866A/CS | TL866II+ | T48 | T56 | T76 |
| --- | --- | --- | --- | --- | --- |
| C.1 start with nothing attached | | | | | |
| C.2 attach and reconnect | | | | | |
| C.3 engine details | | | | | |
| C.4 supported programmers | | | | | |
| C.5 hardware self test | | | | | |
| C.6 chip record and override lists | | | | | |
| C.7 read, twice, against a reference | | | | | |
| C.8 verify, and verify a broken copy | | | | | |
| C.9 blank check, both ways | | | | | |
| C.10 ID read | | | | | |
| C.11 pin check, clean and broken | | | | | |
| C.12 SPI autodetect, filled and empty | | | | | |
| C.13 overrides reach the command line | | | | | |
| C.14 region, config filter, format | | | | | |
| C.15 the advanced options, one at a time | | | | | |
| C.16 cancel, and the programmer after it | | | | | |
| C.17 a second operation while one runs | | | | | |
| C.18 close while an operation runs | | | | | |
| C.19 the write path, sacrificial part | | | | | |
| C.20 the other language, mid-session | | | | | |


## E. Session log

Append a session; never rewrite one.  A corrected session is a new session with
a new date that says which older session it replaces.  Newest first.

```
Session: <date>, <operator>
Host: <distribution>, <kernel>, Qt <version>, PyQt <version>, Python <version>
Engine: <commit as -V reports it>, <commit from git rev-parse HEAD>
Engine databases: <the directory the journal names>
Front-end: <git rev-parse HEAD>, tree clean: <yes/no>
Programmer: <model>, <firmware>, <bus address if it matters>
Parts: reference <part>, sacrificial <part>
Matrix filled: <which columns>

Verdicts:
  C.1  PASS/FAIL/N/A  <what was seen>
  C.2  PASS/FAIL/N/A  <what was seen>
  ...
  C.20 PASS/FAIL/N/A  <what was seen>

Findings:
  <row>, <what the window did>, <what the same command did by hand>,
  <the fix, and the commit it landed in>, <the session that re-ran it>

Not applicable, and why:
  <row>, <reason>, <the README sentence this was copied into>

Journal of the session, pasted verbatim:
  <every journal line, in order, unedited>
```

The pasted journal is not optional.  A verdict without the command lines that
produced it is a claim, and a later reader has no way to tell whether the
window was asked for what the log says it was asked for.
