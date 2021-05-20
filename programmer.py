#!/usr/bin/env python3

import argparse
import crc32c
import os
import secrets
import shlex
import struct
import subprocess
import sys
import tempfile
import typing
import logging

# FLASH content generation
CRC_SIZE = 4
KEY_SIZE = 16
SEGMENT_FORMAT = ">BBQQ{}sHH".format(KEY_SIZE)
BLOCK_FORMAT = ">I{}HI".format(SEGMENT_FORMAT[1:])

# TTN info
APP_EUI = 0x70B3D57ED00003BA
APP_ID = 'meet-je-stad'
FREQUENCY_PLAN = 'EU_863_870_TTN'
# TODO: BasicMAC probably supports 1.0.something at least, check and
# update existing devices too
LORAWAN_VERSION = 'MAC_V1_0'
LORAWAN_PHY_VERSION = 'PHY_V1_0'
DEVICE_ID_TEMPLATE = 'meetstation-{}'

# FLASH upload info
DFU_VIDPID = "0483:df11"
DFU_FLASH_ALT = "0"
DFU_OPTION_ALT = "1"
FLASH_SIZE = 192 * 1024
FLASH_START_ADDRESS = 0x08000000
FLASH_ALIGN = 128
FLASH_PROTECT_SECTOR_SIZE = 4096
PROTECTED_SECTOR = int(FLASH_SIZE / FLASH_PROTECT_SECTOR_SIZE) - 1  # Protect last 4k sector
OPTION_START_ADDRESS = 0x1FF80000
OPTION_BYTES_DEFAULT = [
    0x807000AA,  # FLASH_OPTR: Default values
    0x00000000,  # FLASH_WRPORT1: No protection for 4k sectors 47-16
    0x0000,      # FLASH_WRPROT2: No protection for 4k sectors 15-0
]
OPTION_BYTES_UNPROTECTED = [
    OPTION_BYTES_DEFAULT[0] | 0x60000,  # Enable BOR at Level 5 (±2.8-2.9V)
    OPTION_BYTES_DEFAULT[1],
    OPTION_BYTES_DEFAULT[2],
]
OPTION_BYTES_PROTECTED = [
    OPTION_BYTES_UNPROTECTED[0],
    OPTION_BYTES_UNPROTECTED[1] | (1 << PROTECTED_SECTOR if PROTECTED_SECTOR <= 31 else 0),
    OPTION_BYTES_UNPROTECTED[2] | (1 << (PROTECTED_SECTOR - 32) if PROTECTED_SECTOR >= 32 else 0),
    # Note: DFU uploads to protected pages give no error, but they just
    # do not work
]

# Whether to work with the older dfu-util 0.9 version (autodetected when
# None)
DFU_UTIL_0_9 = None


class BoardInfo(typing.NamedTuple):
    board_id: int
    board_version: int


class EepromContents(typing.NamedTuple):
    crc: int
    board_id: int
    board_version: int
    app_eui: int
    dev_eui: int
    app_key: bytes
    segment_size: int = struct.calcsize(SEGMENT_FORMAT)
    segment_type: int = 0x0201
    total_size: int = struct.calcsize(BLOCK_FORMAT)
    magic: int = 0xB6E03B02


BOARDS = {
    # These are the original boards which use a different flash layout,
    # but this reserves values just in case we switch those boards to
    # this layout later.
    # 'mjs-v1': BoardIdVersion(board_id=0x1, board_version=0x01),
    # 'mjs-v2': BoardIdVersion(board_id=0x1, board_version=0x02),

    # Note that all proto2 boards were initially flashed with an older
    # version of this script that did not include the board_id and
    # board_version (can be detected using the segment size)
    'mjs2020-proto2': BoardInfo(board_id=0x2, board_version=0x01),
    'mjs2020-proto3': BoardInfo(board_id=0x2, board_version=0x02),
    'mjs2020-proto4': BoardInfo(board_id=0x2, board_version=0x03),
    # Fallback, just in case we use this for other boards
    'other': BoardInfo(board_id=0x0, board_version=0x0),
}


def generate_flash(app_eui: int, dev_eui: int, app_key: bytes, board_id: int, board_version: int) -> bytes:
    flash_before_crc = EepromContents(
        crc=0,
        board_id=board_id,
        board_version=board_version,
        app_eui=app_eui,
        dev_eui=dev_eui,
        app_key=app_key,
    )

    # Calculate CRC over all but the CRC bytes
    binary_before_crc = struct.pack(BLOCK_FORMAT, *flash_before_crc)
    flash = flash_before_crc._replace(
        crc=crc32c.crc32c(binary_before_crc[CRC_SIZE:]),
    )

    # And pack again with the right CRC set
    return struct.pack(BLOCK_FORMAT, *flash)


def program_flash(args: argparse.Namespace, flash: bytes, offset: int):
    # Start address must be page-size aligned, otherwise erase fails
    # (note that for EEPROM, writes must be 8-byte aligned, otherwise
    # dfu-util returns success but nothing is written :-S)
    # TODO: Investigate if dfu-util can do this automatically?
    padding = offset % FLASH_ALIGN
    data = b'\x00' * padding + flash
    address = FLASH_START_ADDRESS + offset - padding
    program_dfu(DFU_FLASH_ALT, data, address, noop=args.skip_flash, filename=args.flash_filename)
    if not args.skip_flash:
        verify_dfu(DFU_FLASH_ALT, data, address)


def encode_option_bytes(words: typing.Sequence[int]):
    """ Encode option bytes.

        The passed words is just the option bytes themselves (sequence
        of 32-bit words), this function takes care of splitting into
        16-bit half words, inserting complement half words and little
        endian encoding.
    """
    res = bytes()
    for word in words:
        # Option bytes are stored per half-word, each duplicated for
        # safety (first the original, then a complement)
        for hw in (word & 0xffff, word >> 16):
            res += struct.pack('<HH', hw, hw ^ 0xffff)
    return res


def check_dfu_version():
    output = subprocess.run(['dfu-util', '--version'], check=True, stdout=subprocess.PIPE, text=True).stdout
    return output.startswith('dfu-util 0.9')


def program_option_bytes(args: argparse.Namespace, data: bytes):
    program_dfu(DFU_OPTION_ALT, data, OPTION_START_ADDRESS,
                noop=args.skip_flash, filename=args.option_filename,
                will_reset=True)

    # To read back option bytes:
    # dfu-util -U option.bin --dfuse-address 0x1FF80000 -a 1 && hd option.bin


def program_dfu(alt: str, data: bytes, address: int, filename: str, noop=False, will_reset=False):
    if filename:
        f = open(filename, 'wb')
    else:
        f = tempfile.NamedTemporaryFile(prefix="dfu-programmer", suffix=".bin", delete=True)

    with f:
        f.write(data)
        f.flush()

        addr_arg = hex(address)

        # This tells dfu-util that the board will reset after the write
        # (before reporting succesful status). This happens when writing
        # option bytes. This needs dfu-util 0.10 (released nov 2020).
        # Without it, dfu-util returns failure.
        if will_reset and not DFU_UTIL_0_9:
            addr_arg += ":will-reset"

        cmd = [
            'dfu-util',
            # Device to use
            '-d', DFU_VIDPID,
            # Altsetting determins flash/flash/option bytes
            '-a', alt,
            # Specify address explicitly
            '--dfuse-address', addr_arg,
            # Download to device
            '-D', f.name,
        ]

        if noop:
            logging.info("Not running: %s", shlex.join(cmd))
        else:
            logging.info("Running: %s", shlex.join(cmd))
            if will_reset and DFU_UTIL_0_9:
                # dfu-util 0.9 cannot handle a board reset and will
                # return failure, so try to detect this by capturing
                # dfu-util output. This merges stdout and stderr, to
                # preserve any interleaving when printing again (at the
                # expense of mixing everything in stdout below)
                res = subprocess.run(cmd, check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                sys.stdout.buffer.write(res.stdout)
                sys.stdout.buffer.flush()
                if res.returncode == 74 and b'dfu-util: Error during download get_status' in res.stdout:
                    logging.warning("Ignoring error from dfu-util, it is *probably* only because dfu-util does not "
                                    "handle a reset after writing option bytes.")
                    logging.warning("Using dfu-util 0.10 or above handles this properly.")
                else:
                    # On other errors, let subprocess raise an error as normal
                    res.check_returncode()
            else:
                # With dfu-util 0.10, or when not expecting a reset,
                # just let dfu-util output to the console directly
                subprocess.run(cmd, check=True)


def verify_dfu(alt: str, data: bytes, address: int):
    # DFU will only write to files that do not exist yet, so create
    # a directory it can write into
    with tempfile.TemporaryDirectory(prefix="dfu-programmer") as d:
        filename = os.path.join(d, 'verify.bin')

        addr_arg = hex(address) + ':' + hex(len(data))

        cmd = [
            'dfu-util',
            # Device to use
            '-d', DFU_VIDPID,
            # Altsetting determins flash/flash/option bytes
            '-a', alt,
            # Specify address explicitly
            '--dfuse-address', addr_arg,
            # Upload from device
            '-U', filename,
        ]

        logging.info("Running: %s", shlex.join(cmd))
        subprocess.check_call(cmd)

        with open(filename, 'rb') as f:
            read_back = f.read()
            if read_back != data:
                raise RuntimeError(
                    "Verification of flash failed, data read back was different. Maybe you need to --unprotect first?"
                )


def register_device(args: argparse.Namespace, app_id: str, dev_id: str,
                    app_eui: int, dev_eui: int, app_key: bytes,
                    frequency_plan: str, lorawan_version: str,
                    lorawan_phy_version: str):
    def hex_eui(eui: int) -> str:
        return struct.pack('>Q', eui).hex()

    cmd = [
        'ttn-lw-cli',
        'end-devices',
        'create',
        '--application-id',
        app_id,
        '--device-id',
        dev_id,
        '--dev-eui',
        hex_eui(dev_eui),
        '--root-keys.app-key.key',
        app_key.hex(),
        '--join-eui',
        hex_eui(app_eui),
        '--lorawan-version',
        lorawan_version,
        '--lorawan-phy-version',
        lorawan_phy_version,
        '--frequency-plan-id',
        frequency_plan,
    ]

    if args.skip_register:
        logging.info("Not running: %s", shlex.join(cmd))
    else:
        logging.info("Running: %s", shlex.join(cmd))
        subprocess.check_call(cmd)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(levelname)s: %(message)s',
    )

    parser = argparse.ArgumentParser(description='Initialize a Meetjestad board')
    parser.add_argument('--board', metavar='NAME', required=True, type=str, choices=BOARDS,
                        help='The type of board to flash to (stored in flash). Must be on of: ' + ', '.join(BOARDS))
    parser.add_argument('--id', metavar='ID', required=True, type=int,
                        help='The id for the station to flash')
    parser.add_argument('--flash-filename', metavar='FILE', type=str,
                        help='Write flash to this file instead of an automatic temporary file')
    parser.add_argument('--option-filename', metavar='FILE', type=str,
                        help='Write option bytes to this file instead of an automatic temporary file')
    parser.add_argument('--skip-flash', action='store_true',
                        help='Skip the actual flashing (just show the command that would have been run)')
    parser.add_argument('--skip-register', action='store_true',
                        help='Skip the actual registration with TTN (just show the command that would have been run)')
    parser.add_argument('--unprotect', action='store_true',
                        help='Instead of normal operations, write just option bytes without write protection')

    args = parser.parse_args()

    try:
        global DFU_UTIL_0_9
        if not args.skip_flash and DFU_UTIL_0_9 is None:
            DFU_UTIL_0_9 = check_dfu_version()

        if args.unprotect:
            option = encode_option_bytes(OPTION_BYTES_UNPROTECTED)
            logging.info("Encoded OPTION bytes: %s", option.hex(' ', 4))
            program_option_bytes(args, option)
        else:
            app_eui = APP_EUI
            app_id = APP_ID
            frequency_plan = FREQUENCY_PLAN
            lorawan_version = LORAWAN_VERSION
            lorawan_phy_version = LORAWAN_PHY_VERSION
            dev_eui = args.id
            dev_id = DEVICE_ID_TEMPLATE.format(args.id)
            app_key = secrets.token_bytes(KEY_SIZE)
            board_info = BOARDS[args.board]

            flash = generate_flash(
                app_eui=app_eui, dev_eui=dev_eui, app_key=app_key,
                board_id=board_info.board_id, board_version=board_info.board_version,
            )
            logging.info("Generated FLASH contents: %s", flash.hex(' ', 4))

            if not args.skip_flash:
                logging.info("Programming FLASH...")
            else:
                logging.info("Not programming FLASH...")
            # Put the data at the end of FLASH
            flash_offset = FLASH_SIZE - len(flash)
            program_flash(args, flash, flash_offset)
            if not args.skip_flash:
                logging.info("Programmed FLASH")

            option = encode_option_bytes(OPTION_BYTES_PROTECTED)
            logging.info("Encoded OPTION bytes: %s", option.hex(' ', 4))
            program_option_bytes(args, option)

            if not args.skip_register:
                logging.info("Registering %s on TTN...", dev_id)
            else:
                logging.info("Not registering %s on TTN...", dev_id)
            register_device(
                args, app_id=app_id, dev_id=dev_id, app_eui=app_eui,
                dev_eui=dev_eui, app_key=app_key,
                frequency_plan=frequency_plan,
                lorawan_version=lorawan_version,
                lorawan_phy_version=lorawan_phy_version
            )
            if not args.skip_register:
                logging.info("Registered device on TTN.")

        # Setting option bytes resets
        if not args.skip_flash:
            logging.info("Note: Device was restarted.")
    except Exception as e:
        logging.error(e)


main()
