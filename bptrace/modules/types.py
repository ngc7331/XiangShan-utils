"""Type definitions for bptrace."""

from dataclasses import dataclass, field
from typing import Any, Dict

from .consts import (
    BASE_FIELDS,
    FIELD_STAMP,
    FIELD_BPID,
    FIELD_ADDR,
    FIELD_TRAIN_SOURCE,
    FIELD_TYPE,
    FIELD_TAKEN,
    FIELD_POSITION,
    FIELD_MISPRED,
    FIELD_BRTYPE,
    FIELD_RASACTION,
    FIELD_TARGET
)

@dataclass
class Record:
    """Data class representing a single branch prediction or training record."""
    stamp: int
    id: int
    addr: int
    train_source: int
    type: str
    taken: int
    position: int
    mispredict: int
    br_type: int | None = None
    ras_action: int | None = None
    target: int | None = None
    meta: Dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_db(
        row: tuple,
        include_brtype: bool = False,
        include_rasaction: bool = False,
        include_target: bool = False,
        meta_fields: list[str] | None = None,
    ) -> 'Record':
        """Create Record from database row."""
        record = Record(
            stamp=row[0],
            id=row[1],
            addr=row[2],
            train_source=row[3],
            type=row[4],
            taken=row[5],
            position=row[6],
            mispredict=row[7],
        )
        index = len(BASE_FIELDS)
        if include_brtype:
            record.br_type = row[index]
            index += 1
        if include_rasaction:
            record.ras_action = row[index]
            index += 1
        if include_target:
            record.target = row[index]
            index += 1
        if meta_fields:
            record.meta = {}
            for f in meta_fields:
                record.meta[f] = row[index]
                index += 1
        return record

    @staticmethod
    def render_prunedaddr(addr: int, use_pruned: bool) -> str:
        """Convert pruned address to hex string"""
        return hex(addr << 1) if use_pruned else hex(addr)

    @staticmethod
    def render_brtype(brtype: int) -> str:
        """Convert branch type integer to string representation."""
        return [
            'None',
            'Conditional',
            'Direct',
            'Indirect'
        ][brtype]

    @staticmethod
    def render_rasaction(rasaction: int) -> str:
        """Convert RAS action integer to string representation."""
        return [
            'None',
            'Pop',
            'Push',
            'PopAndPush'
        ][rasaction]

    @staticmethod
    def render_trainsource(source: int | str) -> str:
        """Convert training source integer to string representation."""
        if isinstance(source, str):
            return source
        return [
            "Backend",
            "Ifu"
        ][source]

    def fields(self) -> list[str]:
        """Get list of fields present in this record."""
        fields = BASE_FIELDS.copy()
        if self.br_type is not None:
            fields.append(FIELD_BRTYPE)
        if self.ras_action is not None:
            fields.append(FIELD_RASACTION)
        if self.target is not None:
            fields.append(FIELD_TARGET)
        if self.meta:
            fields.extend(self.meta.keys())
        return fields

    def render(self, use_pruned_addr: bool = False) -> Dict[str, Any]:
        """Convert to dict for CSV output."""
        result = {
            FIELD_STAMP: self.stamp,
            FIELD_BPID: self.id,
            FIELD_ADDR: self.render_prunedaddr(self.addr, use_pruned_addr),
            FIELD_TRAIN_SOURCE: self.render_trainsource(self.train_source),
            FIELD_TYPE: self.type,
            FIELD_TAKEN: self.taken,
            FIELD_POSITION: self.position,
            FIELD_MISPRED: self.mispredict
        }
        if self.br_type is not None:
            result[FIELD_BRTYPE] = self.render_brtype(self.br_type)
        if self.ras_action is not None:
            result[FIELD_RASACTION] = self.render_rasaction(self.ras_action)
        if self.target is not None:
            result[FIELD_TARGET] = self.render_prunedaddr(self.target, use_pruned_addr)
        if self.meta:
            result.update(self.meta)
        return result
