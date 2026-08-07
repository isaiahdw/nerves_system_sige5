# Flashing a Sige5

Four routes. Pick by what the board can currently do.

| board state | route |
| --- | --- |
| Runs Nerves, on the network | 1 — ssh |
| First flash, or unknown state | 4 — maskrom |
| No OS, reaches a U-Boot prompt, bootloader only | 2 — rockusb |
| No OS, needs a filesystem, maskrom unreachable | 3 — ums + fwup |

## 1. ssh

Firmware:

    ./upload.sh <ip>            # or: MIX_TARGET=sige5 mix upload <ip>

The bootloader is not part of an A/B update, so changing it is a separate
write at sector 64:

    sftp <ip> <<< 'put uboot/u-boot-rockchip.bin /tmp/u-boot.bin'
    ssh <ip>
    iex> cmd("dd if=/tmp/u-boot.bin of=/dev/mmcblk0 bs=512 seek=64 conv=fsync")

Verify against the image's own size:

    iex> cmd("dd if=/dev/mmcblk0 bs=512 skip=64 count=<sectors> of=/tmp/rb.bin")
    iex> :crypto.hash(:md5, File.read!("/tmp/rb.bin")) |> Base.encode16(case: :lower)

`<sectors>` is `ceil(bytes / 512)` for the file just written; recompute it for
each image.

The bootloader has no A/B safety net. One copy; a bad one means maskrom.

## 2. rockusb, from a U-Boot prompt

    => mmc dev 0
    => rockusb 0 mmc 0

Then from the host:

    rkdeveloptool wl 64 uboot/u-boot-rockchip.bin
    rkdeveloptool rl 64 <sectors> /tmp/verify.bin    # md5 must match the source
    rkdeveloptool rd                                 # reset

This route is for the bootloader only; use route 4 for a whole image.

## 3. ums + fwup

For a filesystem when maskrom is unreachable. Export the eMMC as a USB disk:

    => mmc dev 0
    => ums 0 mmc 0

Identify it before writing:

    diskutil info /dev/diskN | grep -E "Media Name|Protocol|Disk Size"
      Device / Media Name:  UMS disk 0        <- U-Boot's own gadget name
      Protocol:             USB
      Disk Size:            62.5 GB           <- matches the 58.2 GiB eMMC

Write:

    diskutil unmountDisk /dev/diskN
    sudo fwup -a -t complete -d /dev/rdiskN -i firmware.fw

`/dev/rdiskN`, not `/dev/diskN` — the raw node is minutes instead of a long
wait. macOS offers to initialise the Linux partitions; ignore it.

Without sudo, write the same regions over rockusb, sending only the non-zero
spans at their sector offsets.

## 4. Maskrom

The default for anything larger than a bootloader. Connect the OTG Type-C port
to the host, then hold MASKROM while connecting power to the other (PD-only)
Type-C port.

Whole board from one file:

    fwup -a -d disk.img -t complete -i <firmware>.fw   # raw image on the host

    rkdeveloptool ld                                   # confirm it is listed
    rkdeveloptool db uboot/rk3576_spl_loader_v1.09.108.bin
    rkdeveloptool wl 0 disk.img
    rkdeveloptool rd                                   # reset

Bootloader only, leaving the filesystems alone:

    rkdeveloptool db uboot/rk3576_spl_loader_v1.09.108.bin
    rkdeveloptool wl 64 uboot/u-boot-rockchip.bin
    rkdeveloptool rd

Back up first if anything on the eMMC matters. `db` has to come first: maskrom
by itself cannot read the eMMC, so without a loader in RAM this fails with
`Read LBA failed!`.

    rkdeveloptool db uboot/rk3576_spl_loader_v1.09.108.bin
    rkdeveloptool rl 0 32768 /tmp/backup-16MB.bin

Notes:

- The bootloader is inside the image. `fwup.conf` packages
  `uboot/u-boot-rockchip.bin` and the `complete` task writes it at sector 64
  with everything else, so there is no separate bootloader step.
- `db` is what makes this fast: it puts Rockchip's SPL loader in RAM, and
  everything is written through that.
- To confirm the loader is answering before committing to a write, read a
  sector: `rkdeveloptool rl 64 1 /tmp/s.bin` shows `RKNS` if a bootloader is
  there. The first read straight after `db` can still fail while the loader
  comes up - retry once before concluding anything is wrong.
- `ld` keeps reporting `Maskrom` after `db` succeeds. The mode string does not
  change, so it is not a way to tell whether the loader is loaded; read a
  sector instead.
- The image is sparse — 1808 MB apparent, ~140 MB stored — and `rkdeveloptool`
  sends the apparent size. Through the maskrom loader that costs nothing.

## Layout

From `fwup_include/fwup-common.conf`, in 512-byte sectors:

| sector | what |
| --- | --- |
| 64 | bootloader (`u-boot-rockchip.bin`) |
| 30720 | U-Boot environment, 256 sectors |
| 32768 | boot partition, 524288 |
| 557056 | rootfs A, 1048576 |
| 1605632 | rootfs B, 1048576 |
| 2654208 | app, 1048576 |

The eMMC is `mmc 0` / `mmc@2a330000` / `/dev/mmcblk0`, 58.2 GiB, with a 4 MiB
RPMB. The image is smaller than the disk; first boot grows the app partition to
fill it.

## Which bootloader

`scripts/build-uboot.sh` writes one file, `uboot/u-boot-rockchip.bin`, and fwup
packages it:

| build | secure world |
| --- | --- |
| `./scripts/build-uboot.sh` | none — rkbin BL31 only |
| `SECURE_WORLD=1 ./scripts/build-uboot.sh` | upstream TF-A + OP-TEE, fuses a HUK on first boot |

`uboot/u-boot-rockchip.variant` records which build is in the file, since a
diff of the binary says only "binary files differ".

Rebuilding the system and flashing normally is enough to change it. Moving an
already-running board between stacks is a write at sector 64 and nothing else,
which leaves its filesystem alone.
