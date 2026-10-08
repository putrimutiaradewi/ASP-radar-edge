"""ASTERIX category encoders.

Each module in this package implements the binary encoding of one
EUROCONTROL ASTERIX category (record body + FSPEC). The default
categories replicate — FSPEC-for-FSPEC and byte-for-byte in structure —
the real Juanda (WARR) surveillance feed this project decodes:

    CAT019 - MLAT System Status Messages       (ed 1.2, real feed 1 Hz)
    CAT020 - Multilateration Target Reports    (ed 1.5, 3 record variants)
    CAT021 - ADS-B Target Reports              (ed 0.26, 4 record variants)
    CAT023 - Ground Station / Service Status   (ed 1.2, 2 report types)
    CAT247 - Version Number Exchange           (ed 1.2, every 60 s)

Legacy categories retained for optional use:

    CAT034 - Radar Service Messages            (SUR.ET1.ST05.2000-STD-02b-01)
    CAT048 - Monoradar Target Reports          (SUR.ET1.ST05.2000-STD-04-02)
    CAT062 - System Track Data                 (SUR.ET1.ST05.2000-STD-07-01)
    CAT063 - Sensor Status Messages            (SUR.ET1.ST05.2000-STD-14-01)
"""
