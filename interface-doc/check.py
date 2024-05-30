import logging
from argparse import ArgumentParser
from dataclasses import dataclass
from io import TextIOWrapper
from typing import List, Literal, Tuple

import openpyxl
from openpyxl.worksheet.worksheet import Cell, Worksheet

IGNORED_SHEETS = ["修订记录"]
IGNORED_INTERFACES = {
    "full": ["clock", "reset"],
    "prefix": ["io_perfInfo", "io_fetch_topdown"],
}
SHEET_COLUMNS = {
    "name": 2,
    "width": 4,
    "direction": 3,
}


class VerilogFile:
    def __init__(self, path: str):
        self.__file = open(path, "r")

    def seek(self, offset: int, whence: int = 0):
        self.__file.seek(offset, whence)

    def readline(self) -> str | None:
        line = self.__file.readline()
        if not line:
            return None
        return line.strip()

    def __del__(self):
        self.__file.close()


@dataclass
class Interface:
    name: str
    width: int
    direction: Literal["input", "output"]
    # default_value: int | None = None
    # can_be_x_state: bool | None = None
    # desp: str | None = None

    @staticmethod
    def expand_name(name: str) -> List[str]:
        # expand xxx_0/1_yyy_0/1_zzz to [xxx_0_yyy_0_zzz, xxx_0_yyy_1_zzz, xxx_1_yyy_0_zzz, xxx_0_yyy_1_zzz]
        parts = name.split("_")
        expanded = [parts[0]]
        for part in parts[1:]:
            if "/" not in part:
                expanded = [f"{pre}_{part}" for pre in expanded]
                continue

            nums = part.split("/")
            for num in nums:
                if not num.isdigit():
                    raise ValueError(f"Invalid number({num}) in {name}")
            expanded = [f"{pre}_{num}" for pre in expanded for num in nums]

        return expanded

    @staticmethod
    def from_xlsx(row: Tuple[Cell, ...]) -> List["Interface"]:
        name = row[SHEET_COLUMNS["name"]].value
        if name is None:
            raise ValueError(f"Invalid name({name}), this row may not be an interface")

        try:
            width = int(row[SHEET_COLUMNS["width"]].value)
        except ValueError:
            raise ValueError(f"Invalid width({row[SHEET_COLUMNS['width']].value}) for '{name}', this row may not be an interface")

        direction = row[SHEET_COLUMNS["direction"]].value
        if direction not in ["input", "output"]:
            raise ValueError(f"Invalid direction({direction}) for '{name}', this row may not be an interface")

        # try:
        #     default_value = int(row[5].value)
        # except ValueError:
        #     default_value = None

        # if row[6].value is None:
        #     can_be_x_state = None
        # elif row[6].value.lower() in ["yes", "y", "true"]:
        #     can_be_x_state = True
        # elif row[6].value.lower() in ["no", "n", "false"]:
        #     can_be_x_state = False

        # desp = row[7].value

        return [Interface(
            name=_name.strip(),
            width=width,
            direction=direction,
            # default_value=default_value,
            # can_be_x_state=can_be_x_state,
            # desp=desp,
        ) for _name in Interface.expand_name(name)]

    @staticmethod
    def from_verilog(line: str) -> List["Interface"]:
        line = line.removesuffix(",").split()
        name = line[-1]

        direction = line[0]
        if direction not in ["input", "output"]:
            raise ValueError(f"Invalid direction({direction}) for '{name}', this row may not be an interface")

        if len(line) == 2:
            width = 1
        elif len(line) == 3:
            width = int(line[1].removeprefix("[").removesuffix("]").split(":")[0]) + 1
        else:
            raise ValueError(f"Invalid line: {line}")

        return [Interface(
            name=name,
            width=width,
            direction=direction,
        )]

    def __eq__(self, other: "Interface") -> bool:
        return self.name == other.name and self.width == other.width and self.direction == other.direction

@dataclass
class Module:
    name: str
    interfaces: List[Interface]

    @dataclass
    class DiffResult:
        missing: List[Interface]
        extra: List[Interface]
        diff: List[Tuple[Interface, Interface]]

        def has_diff(self):
            return bool(self.missing or self.extra or self.diff)

        def print_diff(self):
            msg = "\n"
            if self.missing:
                msg += "Missing interfaces:\n- "
                msg += "\n- ".join([f"{i.direction} {i.width} {i.name}" for i in self.missing])
                msg += "\n"
            if self.extra:
                msg += "Extra interfaces:\n- "
                msg += "\n- ".join([f"{i.direction} {i.width} {i.name}" for i in self.extra])
                msg += "\n"
            if self.diff:
                msg += "Different interfaces:\n"
                for a, e in self.diff:
                    assert a.name == e.name
                    msg += f"- {a.name}:\n"
                    if a.width != e.width:
                        msg += f"  width: {a.width} != {e.width}\n"
                    if a.direction != e.direction:
                        msg += f"  direction: {a.direction} != {e.direction}\n"
            logging.error(msg)

    @staticmethod
    def from_xlsx(sheet: Worksheet):
        name = sheet.title
        logging.info(f"Loading module {name} from xlsx...")
        interfaces: List[Interface] = []
        for row in sheet.rows:
            try:
                new = Interface.from_xlsx(row)
                logging.debug(f"... added: {', '.join([_.name for _ in new])}")
                interfaces.extend(new)
            except ValueError as e:
                logging.debug(f"... skipped: {e}")
                continue

        return Module(
            name=name,
            interfaces=interfaces,
        ).filter_interface()

    @staticmethod
    def from_verilog(module_line:str, verilog: TextIOWrapper):
        name = module_line.removeprefix("module ").removesuffix("(")
        logging.info(f"Loading module {name} from verilog...")
        interfaces: List[Interface] = []
        line = verilog.readline()
        while line is not None and line != ");":
            new = Interface.from_verilog(line)
            logging.debug(f"... added: {', '.join([_.name for _ in new])}")
            interfaces.extend(new)
            line = verilog.readline()
        return Module(
            name=name,
            interfaces=interfaces,
        ).filter_interface()

    def diff(self, other: "Module") -> DiffResult:
        missing = [ai for ai in other.interfaces if ai.name not in [i.name for i in self.interfaces]]
        extra = [ai for ai in self.interfaces if ai.name not in [i.name for i in other.interfaces]]
        diff = [(ai, ei) for ai in self.interfaces for ei in other.interfaces if ai.name == ei.name and ai != ei]

        return self.DiffResult(
            missing=missing,
            extra=extra,
            diff=diff,
        )

    def filter_interface(self) -> "Module":
        self.interfaces = [i for i in self.interfaces if i.name not in IGNORED_INTERFACES["full"]]
        self.interfaces = [i for i in self.interfaces if not any([i.name.startswith(prefix) for prefix in IGNORED_INTERFACES["prefix"]])]
        return self

if __name__ == "__main__":
    parser = ArgumentParser()

    parser.add_argument("-v", "--verilog", help="Path to SimTop.v", required=True)
    parser.add_argument("-x", "--xlsx", help="Path to Interface.xlsx", required=True)
    parser.add_argument("--log-level", default=logging.INFO, type=int, help="Log level")

    args = parser.parse_args()

    logging.basicConfig(level=args.log_level)

    # pre-load modules from verilog file
    logging.info("\n\n========== Pre-loading modules from verilog file ==========")
    actuals: List[Module] = []
    verilog = VerilogFile(args.verilog)
    line = verilog.readline()
    while line is not None:
        if not line.startswith("module"):
            line = verilog.readline()
            continue
        actuals.append(Module.from_verilog(line, verilog))
        line = verilog.readline()

    logging.info("\n\n========== Loading & Checking modules from xlsx ==========")
    xlsx = openpyxl.load_workbook(args.xlsx)
    for name in xlsx.sheetnames:
        if name in IGNORED_SHEETS:
            continue

        expect = Module.from_xlsx(xlsx[name])

        actual = [module for module in actuals if module.name == expect.name]
        if len(actual) != 1:
            logging.error(f"Module '{expect.name}' {'not found' if len(actual) == 0 else 'found multiple times'} in verilog")
            continue
        actual = actual[0]

        diff = expect.diff(actual)
        if diff.has_diff():
            logging.error(f"Module '{expect.name}' has differences:")
            diff.print_diff()
