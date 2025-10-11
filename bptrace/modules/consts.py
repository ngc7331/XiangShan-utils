"""Constants for bptrace."""

FIELD_STAMP = "stamp"
FIELD_BPID = "id"
FIELD_ADDR = "addr"
FIELD_TYPE = "type"
FIELD_TAKEN = "taken"
FIELD_POSITION = "position"
FIELD_MISPRED = "mispredict"
FIELD_BRTYPE = "brType"
FIELD_RASACTION = "rasAction"
FIELD_TARGET = "target"

BASE_FIELDS = [
    FIELD_STAMP,
    FIELD_BPID,
    FIELD_ADDR,
    FIELD_TYPE,
    FIELD_TAKEN,
    FIELD_POSITION,
    FIELD_MISPRED
]

KNOWN_FIELDS = BASE_FIELDS + [
    FIELD_BRTYPE,
    FIELD_RASACTION,
    FIELD_TARGET
]

TABLE_PRED = "BpuPredictionTrace"
TABLE_TRAIN = "BpuTrainTrace"
