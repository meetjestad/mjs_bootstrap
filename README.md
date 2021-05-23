MJS2020 device bootstrap script
===============================
This repository contains a python script that can be used to bootstrap
new [STM32-based MJS2020 boards][mjs_pcb], to prepare them with the
right credentials to be used with The Things Network.

The bootstrap process consists of a few steps:
 1. Generate a new random encryption key for this board.
 2. Flash the board type, board number, encryption key and LoraWAN
    application EUI into the board's flash.
 3. Write-protect the flashed settings to prevent accidental overwrites.
 4. Register the board with TTN, using the same board number and
    encryption key.

The data that is written into flash is intended to be used with the
[mjs_firmware][mjs_firmware] sketch (mjs2020 version).

The format used to store the data in flash has no proper name yet, but a
[draft specification is available][layout]. Note that the format is
designed to support multiple independent and variable sized blocks (e.g.
the board manufacturer that adds some production information and then
Meetjestad that adds the TTN info), but this is not supported by this
script yet (it just overwrites any data that is already there). At the
time of writing, the mjs_firmware sketch does not support this yet
either.

Note that for the earlier version of the Meetjestad boards, which were
ATmega328p-based, an [Arduino-based programmer][mjs_programmer] was
used that performs a similar function as this script.

This script is intended to be used within the Meetjestad project by
people with access to the main TTN application of Meetjestad, so it
always registers devices in that TTN application using the Meetjestad
device naming convention. This is not currently configurable, but there
are constants near the top of script that can be modified if needed.

[mjs_pcb]: https://github.com/meetjestad/mjs_pcb/tree/MJS2020
[mjs_programmer]: https://github.com/meetjestad/mjs_programmer
[mjs_firmware]: https://github.com/meetjestad/mjs_firmware/tree/mjs2020
[layout]: https://gist.github.com/matthijskooijman/4ad689aa5ce0853713107fe1097dc432

Prerequisites
=============
This documentation assumes you are using Linux, dependency install
commands are only given for Debian or derivatives (e.g. Ubuntu). Using
other Linux distributions or other operating systems is probably
possible, but untested and you might need to deviate from the
documentation a bit.

Using this script requires a number things:

 - Python3, probably 3.5 or newer, available as `python3` in the path.

 - The [`crc32c`](https://pypi.org/project/crc32c/) python package,
   which can be easily installed using pip:

       $ sudo pip3 install --user crc32c

   Alternatively, you can use apt (but at the time of writing this only
   works on Debian sid and some Ubuntu versions):

       $ sudo apt install python3-crc32c

 - The `dfu-util` command to flash data into the MJS2020 boards (can be
   omitted if you just want to generate flash contents and let someone
   else flash them). On Debian/linux, it can be installed using the
   like-named package:

       $ sudo apt install dfu-util

   Note that ideally you should use dfu-util version 0.10 or later
   (released 2020-11), but at the time of writing, this was not packaged
   for Debian yet. When you use 0.9, the script uses a workaround that
   might cause a failure while protecting the flash to go unnoticed, so if at
   all possible, use 0.10.

 - The `ttn-lw-cli` command to access the TTN network (can be omitted if
   you just want to flash boards and let someone else register them).
   See below for detailed instructions.

 - Set up permissions to access the USB device. Instructions can be
   found [in the mjs_boards
   repository](https://github.com/meetjestad/mjs_boards/tree/stm32l0#linux).

Setting up ttn-lw-cli
---------------------
This script expects the `ttn-lw-cli` command (which offers commandline
access to the TTN configuration backend) to be installed and available
in the path (i.e. you can run it by typing just `ttn-lw-cli` in a
command prompt).

Full instructions can be found on the [the things industries
website][cli-install], but here's a short summary and some additional
notes about using the community TTN backend (which is not super clear in
the documentation).

On Linux, the easiest way to install `ttn-lw-cli` is probably using the
snap, which can also automatically keep itself up-to-date (see the
documentation above for other platforms and other install options):

	$ sudo snap install ttn-lw-stack
	$ sudo snap alias ttn-lw-stack.ttn-lw-cli ttn-lw-cli

After installing, you need a config file. The documentation suggests
copying a config file for The Thing Stack Community Edition (aka The
Things Network), but the `ttn-lw-cli use` command seems to work just as
well for TTN. You have to pass the address of the cluster/deployment
that you want to use. Below example uses the EU1 cluster that Meetjestad
uses, see [this page][ttn-addresses] for other options.

This also uses the `--user` option to generate the config file in a user
configuration directory automatically instead of the default current
directory (where it would not be automatically be found and likely run
into permission issues with the snap too).

	$ ttn-lw-cli use eu1.cloud.thethings.network --user

After configuration, you need to give `ttn-lw-cli` access to your
account:

	$ ttn-lw-cli login

This opens a browser (or shows you a url to open manually), where you
can log in and give `ttn-lw-cli` access.

If you set things up successfully, *and* your account has access to the
`meet-je-stad` application, then you should be able to some application
info with:

	$ ttn-lw-cli applications get meet-je-stad --all

[cli-install]: https://www.thethingsindustries.com/docs/getting-started/cli/installing-cli/
[ttn-addresses]: https://www.thethingsindustries.com/docs/getting-started/ttn/addresses/

Putting the board into bootloader mode
--------------------------------------
To be able to flash a board, it must be connected using the USB-C cable
and it must be in bootloader (DFU) mode. Brand new boards without any
flash contents will likely start up in bootloader mode, but if any
(factory) test code has been flashed previously, that will be running
instead of the bootloader.

To force a board into bootloader mode, keep the BOOT0 button pressed
and briefly press the RESET button.

If no buttons are soldered to the board yet, you can use wires or other
conducting things to short both RESET and BOOT0 pins and then release
RESET first and then BOOT0.

There is no physical feedback that the board is in bootloader mode, but
you can see it appear in the kernel log output (`dmesg -w`) as follows:

	usb 1-1.5.4.7: New USB device strings: Mfr=1, Product=2, SerialNumber=3
	usb 1-1.5.4.7: Product: STM32  BOOTLOADER

It also appears in the output of the `lsusb` command:

        Bus 001 Device 095: ID 0483:df11 STMicroelectronics STM Device in DFU Mode

Running the script
==================
To run the script from a shell, make sure you are in the directory
containing the script and run e.g.:

	./programmer.py --help

This will show a list of accepted options. Below, some common cases are
shown in detail.

Setting up a brand new board
----------------------------
This needs two options: The type of the board, and the number of the
board. For example:

	./programmer.py --board mjs2020-proto4 --id 2022

Remember to put the board into DFU mode first (see above).


This produces quite a bit of output about what is going on, but if
everything went ok, the last two lines should be:

	INFO: Registered device on TTN.
	INFO: Note: Device was restarted.

When TTN registration fails
---------------------------
Keys are generated once and immediately forgotten again, so
if TTN registration fails there is no automatic retry option. If you run
the script again, it will generate new keys. You will have to either:
 - generate new keys and flash them (see below for unprotecting the
   flash in that case), or
 - find the `ttn-lw-cli` command in the failure output, and retry that
   manually after fixing the problem in your setup.

Bootstrapping a board a second time
-----------------------------------
Once a board has been flashed by this script, the configuration data is
write-protected so you cannot just flash it again directly. If you need
to anyway (for example when the TTN registration failed or was later
deleted), you can unprotected the flash with:

	./programmer.py --board mjs2020-proto4 --id 2022 --unprotect

Note that this does not actually use the board and id values passed, but
due to the way the options are implemented, you have to specify some
valid values anyway.

This command requires the board to be in DFU bootloader mode before you
run it and restarts the board (making it leave DFU mode), so do not forget
to force it into bootloader mode again after unprotecting.

After unprotecting the flash, you can flash it again as if it were a
brand new board (see above).

Generating keys for someone else
--------------------------------
Suppose that you have access to TTN to register a new device, but
someone else has the board that needs to be flashed. In this case, you
can generate keys and register the board with TTN, but store everything
into two files instead of flashing into the board:

	./programmer.py --board mjs2020-proto4 --id 2022  --skip-flash --flash-filename flash.bin --option-filename option.bin

This generates `flash.bin` and `option.bin` in the current directory.
Send those files to whomever has the board and let them run `dfu-util`
manually to flash these files into their board (first `flash.bin`, then
`option.bin`). The `dfu-util` commands for this are printed by the above
command (look for `Not running: dfu-util ...`), but are currently the
same for all boards:

	dfu-util -d 0483:df11 -a 0 --dfuse-address 0x802ff80 -D flash.bin
	dfu-util -d 0483:df11 -a 1 --dfuse-address 0x1ff80000:will-reset -D option.bin

When the recipient has dfu-util 0.9, remove the `:will-reset` part.

Letting someone else register with TTN
--------------------------------------
This is another (probably easier) solution when access to TTN and the
board are split between two people. Now the script is used to generate
and flash keys into the board but the TTN registration step is skipped:

	./programmer.py --board mjs2020-proto4 --id 2022  --skip-register

The script prints the `ttn-lw-cli` command that it would have normally
executed (look for a line `INFO: Not running: ttn-lw-cli ...`), so if
you send that command (or the entire script output) to whomever has
access to TTN, they can register the device with the keys you flashed.

License
=======
All files in this repository are licensed under the "Beluki" license:

> Permission is hereby granted, free of charge, to anyone
> obtaining a copy of this document and accompanying files,
> to do whatever they want with them without any restriction,
> including, but not limited to, copying, modification and redistribution.
>
> NO WARRANTY OF ANY KIND IS PROVIDED.
