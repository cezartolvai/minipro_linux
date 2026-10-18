# minipro

## Experimental fork - testing in progress

This fork, and the PyQt5 front-end it adds, are **experimental**.  The regression
suite in `gui/tests/` drives the real window headlessly and covers a good deal of
its behaviour, but automated tests are not a guarantee on real hardware: the
front-end has not been verified against every programmer and target chip this
project supports.  Back up the contents of any chip before a write or erase
operation and verify the result independently.  Feedback, test reports and bug
reports are welcome.

The version number below is the upstream release this fork is based on.  It says
nothing about the maturity of the front-end added here.

## Upstream version 0.7.4

An open source program for controlling XGecu's series of chip programmers.

This program exists because XGecu does not provide a program for use on
Linux or other flavors of Unix.  We who keep this project going prefer a
simple, free, and open-source program that presents a command-line
interface that allows for a GUI front-end if desired.


## This repository

This is a fork of [minipro](https://gitlab.com/DavidGriffith/minipro), taken at
upstream commit `3e2f491` ("Fix T76 SPI autodetect request length", 2026-09-05).
The engine, its chip databases, its documentation and its packaging are
unchanged from upstream.  What this fork adds is the PyQt5 front-end in `gui/`,
the regression suite that guards it, the `make gui-test` target that runs it,
and a CI job that runs it headlessly on every push.

Everything here stays under the GNU General Public License version 3 or later,
which is the license upstream uses.  See `LICENSE` for the license text and
`debian/copyright` for the file-by-file notice.  Upstream remains the place to
take engine bugs and engine patches to.

The remotes are named for that relationship: `origin` is this fork and
`upstream` is the GitLab project.  To bring the fork up to date with engine
work, merge upstream and re-run the suite before publishing:

```nohighlight
git fetch upstream
git merge upstream/master
make gui-test
git push origin master
```

Work lands on this fork and stays there.  Nothing from `gui/` is offered to the
upstream project until the front-end has been exercised on real hardware,
programmer by programmer and chip by chip: the headless suite proves the
promises the window makes to itself, not what a programmer and a chip do back.
Engine bugs and engine patches still belong upstream, where they always did -
what waits for the hardware checks is the front-end.


## Features
* Native support for Linux, BSD, macOS, and other flavors of Unix.
* Compatibility with TL866CS, TL866A, TL866II+, T48, and T56 from XGecu
  (http://www.xgecu.com/en/)
* More than 13000 target devices (including AVRs, PICs, various BIOSes
  and EEPROMs)
* ZIF40 socket and ISP support
* Vendor-specific MCU configuration bits
* Chip ID verification
* Logic IC testing
* Bitbang support
* Overcurrent protection
* System testing

## Synopsis

```nohighlight
$ minipro -p ATMEGA48 -w atmega48.bin
$ minipro -p ATMEGA48 -r atmega48.bin
```

## Prerequisites

You'll need some sort of Linux or MacOS machine.  Other Unices may work,
though that is untested.  You will need version 1.0.16 or greater of
libusb.  Earlier versions may work, but are untested.


## Installation on Linux

### Install build dependencies 

#### Debian/Ubuntu
```nohighlight
sudo apt-get install build-essential pkg-config git libusb-1.0-0-dev zlib1g-dev

```

#### CentOS 7
```nohighlight
sudo yum install gcc make pkgconfig git libusbx-devel
```

#### openSUSE
```nohighlight
sudo zypper install gcc make git-core srecord rpmdevtools libusb-1_0-devel
```

#### Fedora
```nohighlight
sudo dnf install make pkg-config git gcc libusb1-devel zlib-ng-compat-devel
```

### Checkout source code and compile 
```nohighlight
git clone https://gitlab.com/DavidGriffith/minipro.git
cd minipro
make
sudo make install
```

If you have a T56, you'll need to download and install algorithms from
Xgeku's official software package.
```nohighlight
sudo make install-algorithm
```

### Udev configuration (recommended)
If you want to access the programmer as a regular user, you'll have to
configure udev to recognize the programmer and set appropriate access
permissions.

```nohighlight
sudo cp udev/*.rules /etc/udev/rules.d/
sudo udevadm trigger
```
You'll also have to add your regular user to the `plugdev` system
group:
```nohighlight
sudo usermod -a -G plugdev YOUR-USER
```
Note that this change will only become effective after your next
login.

### Bash completion (optional)

Bash users may want to optionally install the provided completion file:
```nohighlight
sudo cp bash_completion.d/minipro /etc/bash_completion.d/
```

### Making a .deb package

Building a Debian package directly from this repository is easy.  Make
sure you have the build dependencies installed as described above.  Be
sure it all builds, then do this:

```nohighlight
sudo apt-get install fakeroot debhelper dpkg-dev
fakeroot dpkg-buildpackage -b -us -uc
```

You should then have a .deb package for you to install with `dpkg -i`. 
Note that the .deb package will already provide the udev and 
bash-completion configurations for you.

### Making a Debian source package for a PPA

The following commands require `git-buildpackage`:

```nohighlight
git archive --format=tar HEAD | tar x && git commit -am "packaging: Makefile substitution"
gbp dch --commit --since=HEAD --upstream-branch=master --dch-opt="-lppa" --dch-opt="-D$(lsb_release -c -s)" debian
gbp buildpackage --git-upstream-tree=SLOPPY --git-export-dir=../build-area -S
```

You can then `dput` the `*.changes` file in `../build-area` to your PPA.

### Making a .rpm package

You can build RPM packages for Fedora, CentOS and openSUSE with the supplied
`rpm/minipro-*.spec` files.

First make sure you have a RPM build environment set up. You need to have
the rpmdevtools package installed and a `rpmbuild` directory tree within
your homedir. Use the `rpmdev-setuptree` command to create the rpmbuild
directory tree if it does not exist yet.

Then use these commands to download the source tarballs from Gitlab and
build the package for Fedora and CentOS:

```nohighlight
spectool -g -R rpm/minipro-fedora.spec
rpmbuild -ba rpm/minipro-fedora.spec
```

Or for openSUSE:

```nohighlight
rpmdev-spectool -f -g -R rpm/minipro-opensuse.spec
rpmbuild -ba rpm/minipro-opensuse.spec
```

The final RPMs can be found below `~/rpmbuild/RPMS/`

## Installation on Guix

```nohighlight
guix install minipro
```

### Udev configuration (Guix System)

If you want to access the programmer without root privileges you must install the necessary udev rules. This can be done by extending `udev-service-type` in your `operating-system` configuration with this package.


```nohighlight
(udev-rules-service 'minipro minipro #:groups '("plugdev")
```

Additionally your user must be member of the `plugdev` group. At that point, minipro should be installed and ready to use.

## Installation on macOS

The easiest way to install on macOS is by using [homebrew](https://brew.sh/) to install the most recent release:

```nohighlight
brew install minipro
```

At that point, minipro should be installed and ready to use.

If you'd rather compile from source (if, for instance, you need newer code than is in the most recent release), you'll need to do the following to prepare your environment and compile the code.

### Install dependencies
Install `pkg-config` and `libusb` using brew or MacPorts:
```
brew install pkg-config
brew install libusb
brew link libusb
```
or:
```
port install pkgconfig
port install libusb
```
### Checkout source code and compile
```nohighlight
git clone git@gitlab.com:DavidGriffith/minipro.git
cd minipro
make
sudo make install
```

### Download and install bitstream algorithms for the T56

Unlike the other programmers from Xgecu, the T56 does not store its FPGA
bitstreams in the hardware.  Instead it must be loaded by the
controlling software.  Those bitstreams (Xgecu calls them algorithms)
are found in the official Xgecu software releases.  They cannot be
included here for copyright reasons.  Instead the build process can
download them for you and convert them into a single file for use by
`minipro`.
```nohighlight
make algorithm
sudo make install-algorithm
```


### Checksum notes

When you download an archive/tarball from Gitlab, there is no guarantee
that the file given to you will hash to the same value time and time
again.  For Gitlab to allow for this would require it to store a very
large amount of data.  If you run a packaging system that depends on
hashes like that, consider following the example of the FreeBSD ports
system.  What they do is use the SHA-1 commit hash of the version to be
installed.  Check out how FreeBSD does it for Minipro here:
https://github.com/freebsd/freebsd-ports/tree/main/sysutils/minipro

### Attributions

Code and data for testing ICs have been borrowed from
https://github.com/Johnlon/integrated-circuit-tester and
https://github.com/akshaybaweja/Smart-IC-Tester and used in accordance
with the MIT License (https://opensource.org/license/MIT)

## GUI front-end

A PyQt5 front-end for the command-line program lives in `gui/`.  It drives the
same `minipro` engine and adds no device access of its own, so everything the
command line can do is reachable through it.  This front-end is experimental;
see the notice near the top of this file before using it on hardware you care
about.

### Running the front-end

Build the engine first, then start the front-end from the source tree:

```nohighlight
make
python3 gui/minipro_gui.py
```

The engine is looked for beside the front-end, in the directory above it, and
in the usual install locations.  A build kept elsewhere is named with
`--binary`:

```nohighlight
python3 gui/minipro_gui.py --binary ../build/minipro
```

The chip databases are taken from the directory named by `MINIPRO_HOME`, or
from the directory the engine was found in.

### The language of the front-end

The front-end speaks English and Romana, and opens in whichever language it
chose last.  The choice is made from the `View` menu (`Vedere`, under
`Limba`, in the Romanian window) and is remembered between runs, so a window
stuck in a language the operator cannot read is escapable: each entry in the
language menu is written in the language it names.

The Romanian text is written without diacritics on purpose.  A terminal or a
font that cannot draw them turns those letters into boxes, and the warning the
front-end carries has to stay readable on the machine it warns.

The window also keeps a permanent banner above its controls saying that it is
experimental.  A modal warning at startup is dismissed and gone, so it teaches
nothing about the operation the operator is about to run; the banner stays
readable while a write or an erase is in progress, which is the one moment it
matters.

### Installing PyQt5

The front-end needs Python 3 and PyQt5.

On Debian or Ubuntu:
```nohighlight
sudo apt install python3-pyqt5
```

On Fedora:
```nohighlight
sudo dnf install python3-qt5
```

On macOS:
```nohighlight
brew install pyqt5
```

### Testing the front-end

```nohighlight
make gui-test                         sealed checks, no hardware involved
MINIPRO_TEST_ENGINE=1 make gui-test   plus the engine database checks
```

The suite drives the real window headlessly and answers every dialog the window
raises, so it needs no display of its own.  On a machine with no display at all,
name the platform Qt should use:

```nohighlight
QT_QPA_PLATFORM=offscreen make gui-test
```

Every process a run starts is recorded with the creation time its process number
was born with, so cleanup reaches only the processes that run started and never
a process number the operating system handed to something else.
