## gpu-opp-table

Voltage-grade buckets:

| pvtm | grade |
| --- | --- |
| 0-800 | L0 |
| 801-820 | L1 |
| 821-840 | L2 |
| 841-860 | L3 |
| 861-9999 | L4 |

- `rockchip,pvtm-volt` = 750000 uV
- `rockchip,low-temp` = 15 degC
- `rockchip,low-temp-min-volt` = 750000 uV
- `rockchip,temp-hysteresis` = 5 degC
- `rockchip,pvtm-ref-temp` = 35 degC
- `rockchip,pvtm-temp-prop` = 0, 0
- `volt-mem-read-margin` = 855000uV->1, 765000uV->2, 675000uV->3, 495000uV->4

| MHz | base | L1 | L2 | L3 | L4 |
| --- | --- | --- | --- | --- | --- |
| 300 | 700000 |  |  |  |  |
| 400 | 700000 |  |  |  |  |
| 500 | 700000 |  |  |  |  |
| 600 | 700000 |  |  |  |  |
| 700 | 725000 | 712500 | 700000 | 700000 | 700000 |
| 800 | 775000 | 762500 | 750000 | 737500 | 725000 |
| 900 | 825000 |  | 812500 | 800000 | 787500 |
| 950 | 850000 |  | 837500 | 825000 | 812500 |

## npu-opp-table

Voltage-grade buckets:

| pvtm | grade |
| --- | --- |
| 0-796 | L0 |
| 797-816 | L1 |
| 817-836 | L2 |
| 837-856 | L3 |
| 857-9999 | L4 |

- `rockchip,pvtm-volt` = 750000 uV
- `rockchip,low-temp` = 15 degC
- `rockchip,low-temp-min-volt` = 750000 uV
- `rockchip,temp-hysteresis` = 5 degC
- `rockchip,pvtm-ref-temp` = 35 degC
- `rockchip,pvtm-temp-prop` = 0, 0
- `volt-mem-read-margin` = 855000uV->1, 765000uV->2, 675000uV->3, 495000uV->4

| MHz | base | L1 | L2 | L3 | L4 |
| --- | --- | --- | --- | --- | --- |
| 300 | 725000 |  |  |  |  |
| 400 | 725000 |  |  |  |  |
| 500 | 725000 |  |  |  |  |
| 600 | 725000 |  |  |  |  |
| 700 | 750000 | 737500 | 725000 | 725000 | 725000 |
| 800 | 775000 | 762500 | 750000 | 737500 | 725000 |
| 900 | 800000 | 787500 | 775000 | 762500 | 750000 |
| 1000 | 850000 |  |  | 837500 | 825000 |

## cluster0-opp-table

Voltage-grade buckets:

| pvtm | grade |
| --- | --- |
| 0-1939 | L0 |
| 1940-1969 | L1 |
| 1970-1999 | L2 |
| 2000-2029 | L3 |
| 2030-2059 | L4 |
| 2060-9999 | L5 |

- `rockchip,pvtm-freq` = 1800000 kHz
- `rockchip,pvtm-volt` = 850000 uV
- `rockchip,low-temp` = 15 degC
- `rockchip,temp-hysteresis` = 5 degC
- `rockchip,pvtm-ref-temp` = 35 degC
- `rockchip,pvtm-temp-prop` = 890, 890
- `volt-mem-read-margin` = 855000uV->1, 765000uV->2, 675000uV->3, 495000uV->4

| MHz | base | L1 | L2 | L3 | L4 | L5 |
| --- | --- | --- | --- | --- | --- | --- |
| 408 | 700000 |  |  |  |  |  |
| 600 | 700000 |  |  |  |  |  |
| 816 | 700000 |  |  |  |  |  |
| 1008 | 700000 |  |  |  |  |  |
| 1200 | 700000 |  |  |  |  |  |
| 1416 | 725000 | 712500 | 700000 | 700000 | 700000 | 700000 |
| 1608 | 750000 | 750000 | 737500 | 737500 | 725000 | 712500 |
| 1800 | 825000 | 825000 | 812500 | 800000 | 787500 | 775000 |
| 2016 | 900000 | 887500 | 875000 | 862500 | 850000 | 837500 |
| 2208 | 950000 | 937500 | 925000 | 912500 | 900000 | 887500 |

## cluster1-opp-table

Voltage-grade buckets:

| pvtm | grade |
| --- | --- |
| 0-2065 | L0 |
| 2066-2095 | L1 |
| 2096-2125 | L2 |
| 2126-2155 | L3 |
| 2156-2185 | L4 |
| 2186-9999 | L5 |

- `rockchip,pvtm-freq` = 1800000 kHz
- `rockchip,pvtm-volt` = 850000 uV
- `rockchip,low-temp` = 15 degC
- `rockchip,temp-hysteresis` = 5 degC
- `rockchip,pvtm-ref-temp` = 35 degC
- `rockchip,pvtm-temp-prop` = 920, 920
- `volt-mem-read-margin` = 855000uV->1, 765000uV->2, 675000uV->3, 495000uV->4

| MHz | base | L1 | L2 | L3 | L4 | L5 |
| --- | --- | --- | --- | --- | --- | --- |
| 408 | 700000 |  |  |  |  |  |
| 600 | 700000 |  |  |  |  |  |
| 816 | 700000 |  |  |  |  |  |
| 1008 | 700000 |  |  |  |  |  |
| 1200 | 700000 |  |  |  |  |  |
| 1416 | 712500 | 700000 | 700000 | 700000 | 700000 | 700000 |
| 1608 | 737500 | 725000 | 712500 | 700000 | 700000 | 700000 |
| 1800 | 800000 | 787500 | 775000 | 762500 | 750000 | 737500 |
| 2016 | 862500 | 850000 | 837500 | 825000 | 812500 | 800000 |
| 2208 | 925000 | 912500 | 900000 | 887500 | 875000 | 862500 |
| 2304 | 950000 | 937500 | 925000 | 912500 | 900000 | 887500 |

## dmc-opp-table

- `rockchip,low-temp` = 15 degC
- `rockchip,low-temp-min-volt` = 750000 uV
- `rockchip,temp-hysteresis` = 5 degC

| MHz | base | L1 |
| --- | --- | --- |
| 528 | 725000 | 700000 |
| 1068 | 725000 | 700000 |
| 1560 | 725000 | 725000 |
| 2736 | 800000 | 775000 |

## vop-opp-table

- `rockchip,low-temp` = 15 degC
- `rockchip,low-temp-min-volt` = 750000 uV
- `rockchip,temp-hysteresis` = 5 degC

| MHz | base | L1 |
| --- | --- | --- |
| 500 | 700000 |  |
| 594 | 750000 | 725000 |
| 702 | 750000 | 725000 |

