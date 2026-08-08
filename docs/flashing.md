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

## Before a maskrom flash

A maskrom write replaces the app partition, so three things that live there are
gone afterwards. All three have bitten someone; none of them look like a
failure at the time.

- **WiFi credentials are compiled in, so export them before `mix firmware`.**

      export SIGE5_WIFI_SSID="..." SIGE5_WIFI_PSK="..."
      MIX_TARGET=sige5 mix firmware

  Without them the build prints `==> no WiFi configuration in this image` and
  carries on. The image looks identical and the board comes up with no route,
  which on a wlan0-only board means the console is the only way back. Read the
  build output rather than tailing the last few lines of it.

- **The SSH host key is regenerated**, because it lives in `/data`. The next
  connection fails with `REMOTE HOST IDENTIFICATION HAS CHANGED`. That is
  expected here and not a reason to disable host key checking permanently:

      ssh-keygen -R <ip>

- **A provisioned secure-world identity is not lost, but its token is.** The
  HUK is in fuses and survives; the PKCS#11 token and the device certificate
  are in `/data` and do not. Re-run `mix sige5.provision <ip>` afterwards, and
  register the new certificate - the key is new, so the old one is orphaned.

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
- **The first command after `db` can fail while the loader comes up, and this
  applies to writes as well as reads.** `Write LBA failed!` on the first
  attempt, immediately followed by a clean 100% write on the second, is the
  usual shape. Retry once before concluding anything is wrong.
- To confirm the loader is answering before committing to a write, ask it what
  it can see:

      rkdeveloptool rfi          # Flash Info: manufacturer, size, sector count

  A Sige5 answers `SAMSUNG`, `59640 MB`, `122142720 Sectors`. That is a better
  check than reading a sector, because it fails clearly when the loader is up
  but storage is not, and on a fresh board sector 64 is blank so `rl` tells you
  nothing either way.
- `ld` keeps reporting `Maskrom` after `db` succeeds. The mode string does not
  change, so it is not a way to tell whether the loader is loaded; use `rfi`.
- The image is sparse — 1808 MB apparent, ~140 MB stored — and `rkdeveloptool`
  sends the apparent size. Through the maskrom loader that costs nothing.

## Serial console

40-pin header: pin 8 (TX), pin 10 (RX), pin 6 (GND), 1500000 8N1. It is the
only way in when there is no network.

**On macOS, pick the terminal carefully.** `stty` cannot set 1500000 - it
answers `tcsetattr: Invalid argument` - so anything driving the port through it
fails, and falling back to 115200 produces convincing garbage rather than an
error, which is worse than no output. Non-standard rates need the
`IOSSIOSPEED` ioctl (`0x80085402`, `_IOW('T', 2, speed_t)`); use a terminal
that issues it, or a few lines of Python that do.

**Zero bytes is the healthy state.** A booted board at an idle prompt prints
nothing, so a passive read returning nothing looks exactly like a dead board
and is not one - bootloops and panics are the noisy cases. Send a newline and
wait for the prompt before concluding anything.

To bring up WiFi from the console on an image built without credentials:

    n = [%{key_mgmt: :wpa_psk, ssid: "...", psk: "..."}]
    VintageNet.configure("wlan0", %{type: VintageNetWiFi,
      vintage_net_wifi: %{networks: n}, ipv4: %{method: :dhcp}})

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
