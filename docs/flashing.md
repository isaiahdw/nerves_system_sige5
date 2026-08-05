# Flashing a Sige5

Four ways to get bytes onto the eMMC, in the order worth reaching for. Every
one of these was worked out the hard way at least once; the notes are the
mistakes, not the theory.

## 1. Board runs Nerves and is on the network

The normal case. Firmware goes over ssh:

    ./upload.sh <ip>            # or: MIX_TARGET=sige5 mix upload <ip>

The bootloader is not part of an A/B firmware update, so changing it is a
separate step - copy it over and write it at sector 64:

    sftp <ip> <<< 'put uboot/u-boot-rockchip-ostee.bin /tmp/u-boot.bin'
    ssh <ip>
    iex> cmd("dd if=/tmp/u-boot.bin of=/dev/mmcblk0 bs=512 seek=64 conv=fsync")

Verify by reading back **the image's own size**, not a remembered one:

    iex> cmd("dd if=/dev/mmcblk0 bs=512 skip=64 count=<sectors> of=/tmp/rb.bin")
    iex> :crypto.hash(:md5, File.read!("/tmp/rb.bin")) |> Base.encode16(case: :lower)

`<sectors>` is `ceil(bytes / 512)` for the file being written. It changes
between builds - a count carried over from a previous image reads a short
buffer and reports a mismatch that is not real.

There is no A/B safety net on the bootloader. One copy, and a bad one means
maskrom.

## 2. Board has no OS, but reaches a U-Boot prompt

`rkdeveloptool` can drive U-Boot's rockusb gadget.

    => mmc dev 0
    => rockusb 0 mmc 0

**`mmc dev 0` first.** Without it the gadget still enumerates and reads still
work, but every write fails with `failed writing to device mmc: 0` on the
console, and the session then wedges so thoroughly that reads stop too and
`rkdeveloptool db` reports "Downloading bootloader failed!". That looks exactly
like a dead board and is not one - spam Ctrl-C on the console to get the prompt
back. No power cycle needed.

Then, from the host:

    rkdeveloptool wl 64 uboot/u-boot-rockchip-ostee.bin
    rkdeveloptool rl 64 <sectors> /tmp/verify.bin    # md5 must match the source
    rkdeveloptool rd                                 # reset

`rkdeveloptool ld` prints `Maskrom` for both true maskrom and a U-Boot rockusb
gadget, so the mode string tells you nothing. Try a read to find out which you
have.

### Speed

This path runs well under 1 MB/s. Fine for a 10 MB bootloader, useless for a
filesystem: a 1.8 GB image did not finish in 50 minutes. Size the job before
starting it.

## 3. Board has no OS and needs a filesystem

Do **not** materialise a full disk image and write it end to end. `fwup`
already writes sparsely, and a Sige5 `complete` image is about 7 percent data:

    fwup -a -d /tmp/board.img -i firmware.fw -t complete    # 1808 MB
    # of which only ~137 MB is non-zero

The rest is partition padding that first boot fills in anyway.

Expose the eMMC as a USB disk from U-Boot and let fwup do it properly:

    => mmc dev 0
    => ums 0 mmc 0

The host sees a removable disk - confirm it before writing anything:

    diskutil info /dev/diskN | grep -E "Media Name|Protocol|Disk Size"
      Device / Media Name:  UMS disk 0        <- U-Boot's own gadget name
      Protocol:             USB
      Disk Size:            62.5 GB           <- matches the 58.2 GiB eMMC

Then:

    diskutil unmountDisk /dev/diskN
    sudo fwup -a -t complete -d /dev/rdiskN -i firmware.fw

`/dev/rdiskN`, not `/dev/diskN` - the raw node is the difference between a
couple of minutes and a long wait. macOS will offer to initialise the Linux
partitions; ignore it.

If sudo is not available, the same regions can go over rockusb instead by
writing only the non-zero spans at their sector offsets. Slow, but it needs no
privileges.

## 4. Maskrom

For a board that will not reach U-Boot. Same protocol and same speed as
route 2.

    rkdeveloptool ld                                    # confirm it is listed
    rkdeveloptool db uboot/rk3576_spl_loader_v1.09.108.bin
    rkdeveloptool wl 64 uboot/u-boot-rockchip.bin
    rkdeveloptool rd

`db` loads a loader into RAM; without it, LBA commands on a true maskrom device
silently do nothing and reads come back empty.

Back up before overwriting anything you might want again:

    rkdeveloptool rl 0 32768 /tmp/backup-16MB.bin

## Layout

From `fwup_include/fwup-common.conf`, in 512-byte sectors:

| sector | what |
| --- | --- |
| 64 | bootloader (`u-boot-rockchip*.bin`) |
| 30720 | U-Boot environment, 256 sectors |
| 32768 | boot partition, 524288 |
| 557056 | rootfs A, 1048576 |
| 1605632 | rootfs B, 1048576 |
| 2654208 | app, 1048576 |

The eMMC is `mmc 0` / `mmc@2a330000` / `/dev/mmcblk0`, 58.2 GiB. `mmc info`
from U-Boot also reports "Boot area 0 is not write protected" and a 4 MiB RPMB.

## Which bootloader

`scripts/build-uboot.sh` produces three, and only the first is packaged by
fwup:

| file | secure world |
| --- | --- |
| `u-boot-rockchip.bin` | none - Rockchip BL31 only |
| `u-boot-rockchip-bl32.bin` | Rockchip BL31 + BL32 (`WITH_BL32=1`) |
| `u-boot-rockchip-ostee.bin` | upstream TF-A + OP-TEE (`USE_OPENSOURCE_TEE=1`) |

Swapping between them is a bootloader write at sector 64 and nothing else, so a
board can be moved between firmware stacks without touching its filesystem.
