from dataclasses import dataclass
from enum import Enum
import glob
import watchdog
import watchdog.observers
import watchdog.events
import time
import os
import copy

EQ_PATH = "eqapo-files/"
EQAPO_DIR = r"C:\Program Files\EqualizerAPO\config\compiled"

class PassKind(Enum):
    High = 0
    Low = 1
    Band = 2

@dataclass
class EqFilter:
    def __str__(self) -> str:
        return "# Unknown filter"

@dataclass
class FreqFilter(EqFilter):
    freq: str

@dataclass
class GainFilter(EqFilter):
    gain: str

@dataclass
class PreampFilter(GainFilter):

    # eq file example:
    #   gain -6dB;

    def __str__(self) -> str:
        return f"Preamp: {self.gain} dB"

@dataclass
class PeakingFilter(FreqFilter, GainFilter):
    q: str

    # eqapo example:
    #   Filter: ON PK Fc 100 Hz Gain 0 dB Q 10.0007
    
    # eq file example:
    #   PK @100, 0dB, 10.0007;

    def __str__(self) -> str:
        return f"Filter: ON PK Fc {self.freq} Hz Gain {self.gain} dB Q {self.q}"

class PanningFilter(EqFilter):
    """
    Wraps any filter that has a gain component, and overrides
    the gain to do a balanced pan between two channels.
    """

    def __init__(self, filter: EqFilter, posChannel: str, negChannel: str, enclosingChannels: list[str]):
        self.posFilter = copy.deepcopy(filter)
        self.negFilter = copy.deepcopy(filter)
        baseGain = self.posFilter.gain if isinstance(self.posFilter, GainFilter) else "0"
        if baseGain.startswith("-"):
            baseGain = baseGain[1:]
            posChannel, negChannel = negChannel, posChannel
        self.posChannel = posChannel
        self.negChannel = negChannel
        self.enclosingChannels = enclosingChannels
        if isinstance(self.posFilter, GainFilter) and isinstance(self.negFilter, GainFilter):
            self.posFilter.gain = f"{baseGain}"
            self.negFilter.gain = f"-{baseGain}"
    
    # eq file example:
    #   pan R, L, { PK @4300, 3dB, 3.8 };
    # would be the equivalent of
    #   R:
    #       PK @4300, 3dB, 3.8;
    #   L:
    #       PK @4300, -3dB, 3.8;

    def __str__(self) -> str:
        if not isinstance(self.posFilter, GainFilter):
            return str(self.posFilter)
        
        res = f"Channel: {self.posChannel}\n"
        res += str(self.posFilter) + "\n"
        res += f"Channel: {self.negChannel}\n"
        res += str(self.negFilter) + "\n"
        res += f"Channel: {' '.join(self.enclosingChannels)}"
        return res

@dataclass
class PassFilter(FreqFilter):
    kind: PassKind
    q: str

    # eqapo example:
    #   Filter: ON HPQ Fc 291.76 Hz Q 0.9012

    # eq file example:
    #   HP @291.76, 0.9012;

    def __str__(self) -> str:
        fType = ["HPQ", "LPQ", "BP"][self.kind.value]
        return f"Filter: ON {fType} Fc {self.freq} Hz Q {self.q}"

@dataclass
class ShelfFilter(FreqFilter, GainFilter):
    kind: PassKind
    q: str

    # eqapo example:
    #   Filter: ON HSC Fc 91.82 Hz Gain 4.7 dB Q 1.0492

    # eq file example:
    #   HS @91.82, 4.7, 1.0492;

    def __str__(self) -> str:
        if self.kind == PassKind.Band:
            return f"# Band shelf filter not supported"
        fType = ["HSC", "LSC", "BS"][self.kind.value]
        return f"Filter: ON {fType} Fc {self.freq} Hz Gain {self.gain} dB Q {self.q}"



@dataclass
class AllPassFilter(FreqFilter):
    q: str

    # eqapo example:
    #   Filter: ON AP Fc 224.4 Hz Q 8.0328
    
    # eq file example:
    #   AP @224.4, 8.0328;

    def __str__(self) -> str:
        return f"Filter: ON AP Fc {self.freq} Hz Q {self.q}"

@dataclass
class DelayFilter(EqFilter):
    delay: str
    useSamples: bool = True # Use samples or milliseconds

    # eqapo example:
    #   Delay: 50 ms
    #   Delay: 480 samples

    # eq file example:
    #   delay 50ms;
    #   delay 480samples;

    def __str__(self) -> str:
        return f"Delay: {self.delay} {'samples' if self.useSamples else 'ms'}"

@dataclass
class CopyFilter(EqFilter):
    copyExpressions: list[str]

    # eqapo example:
    #   Copy: L=L+0.5*R
    #   Copy: L=R+-6dB*C
    #   Copy: 1=R R=0.5
    #   Copy: LFE=L L=0.0 R=0.0 C=0.0 RL=0.0 RR=0.0

    # eq file example:
    #   L=L+0.5*R;
    #   L=R+-6dB*C;
    #   1=R R=0.5;
    #   LFE=L L=0.0 R=0.0 C=0.0 RL=0.0 RR=0.0;

    def __str__(self) -> str:
        return f"Copy: {' '.join(self.copyExpressions)}"

class EqFilterList:
    """Encapsulates a list of filters"""

    def __init__(self, indent: int = 0):
        self.indent = indent
        self._filters: list[EqFilter] = []

    def addFilter(self, filter: EqFilter):
        self._filters.append(filter)
    
    def __len__(self):
        return len(self._filters)
    
    def __getitem__(self, i: int):
        return self._filters[i]
    
    def __iter__(self):
        return iter(self._filters)

    def __str__(self) -> str:
        res = ""
        for f in self._filters:
            res += ' ' * self.indent
            res += str(f) + "\n"
        return res

class EqFilterGroup:
    """Encapulates channel selection"""

    # eqapo example:
    #   Channel: L
    #   Channel: R C
    #   Channel: all

    def __init__(self, indent = 0, channels: list[str] | None = None):
        if channels is None:
            channels = ['all']
        self.channels: list[str] = channels
        self.filters: EqFilterList = EqFilterList(indent)

    def __str__(self) -> str:
        res = f"Channel: {' '.join(self.channels)}\n"
        for f in self.filters:
            res += str(f) + "\n"
        return res

class EqFile:
    def __init__(self, name: str):
        self.name = name
        self.groups: list[EqFilterGroup] = []
        self._directives: dict[str, list[str]] = {}
    
    def toEqApo(self) -> str:
        res = ""
        for g in self.groups:
            res += str(g)
        return res
    
    def addDirective(self, directive: str, args: str):
        if not directive in self._directives:
            self._directives[directive] = []
        self._directives[directive].append(args)
    
    def getDirective(self, directive: str) -> list[str]:
        return self._directives.get(directive, [])

class ArgumentType(Enum):
    Raw = 0
    Frequency = 1
    Decibels = 2
    Q = 3
    Milliseconds = 4
    Samples = 5,
    Channel = 6,
    Filter = 7

class Argument:
    def __init__(self, arg: str):
        self.rawArgument = arg
        self.type = ArgumentType.Raw
        self.value = arg
        self.consumed = False

        # possible prefixes:
        #   @ - frequency
        #   Q= - Q

        # possible suffixes:
        #   Hz - frequency (alt)
        #   dB - decibels
        #   ms - milliseconds
        #   samples - samples

        # possible channel names:
        #   L, R, C, LFE, RL, RR, SL, SR

        # Filter is just enclosed in curly braces

        if arg.startswith("{") and arg.endswith("}"):
            self.type = ArgumentType.Filter
            self.value = arg[1:-1].strip()

        if arg in ["L", "R", "C", "LFE", "RL", "RR", "SL", "SR"]:
            self.type = ArgumentType.Channel
            self.value = arg

        if arg.startswith("@"):
            self.type = ArgumentType.Frequency
            self.value = arg[1:].strip()
        if arg.endswith("Hz"):
            self.type = ArgumentType.Frequency
            self.value = arg[:-2].strip()
        
        if self.type == ArgumentType.Raw:

            if arg.startswith("Q="):
                self.type = ArgumentType.Q
                self.value = arg[2:].strip()

            elif arg.endswith("dB"):
                self.type = ArgumentType.Decibels
                self.value = arg[:-2].strip()

            elif arg.endswith("ms"):
                self.type = ArgumentType.Milliseconds
                self.value = arg[:-2].strip()

            elif arg.endswith("samples"):
                self.type = ArgumentType.Samples
                self.value = arg[:-6].strip()
        
        # added these because i wasnt finished typing and it commited 910000hz
        # and blasted a saw wave
        if self.type == ArgumentType.Frequency:
            try:
                self.value = str(min(max(float(self.value), 1), 20000))
            except ValueError:
                self.value = "1000"
        
        if self.type == ArgumentType.Q:
            try:
                self.value = str(min(max(float(self.value), 0.05), 10))
            except ValueError:
                self.value = "1"
        
        if self.type == ArgumentType.Milliseconds:
            try:
                self.value = str(min(max(int(self.value), 0), 1000))
            except ValueError:
                self.value = "0"
        
        if self.type == ArgumentType.Samples:
            try:
                self.value = str(min(max(int(self.value), 0), 1000))
            except ValueError:
                self.value = "0"
        
        if self.type == ArgumentType.Decibels:
            try:
                self.value = str(min(max(float(self.value), -40), 20))
            except ValueError:
                self.value = "0"
    
    def consume(self):
        self.consumed = True
        return self
    
    @staticmethod
    def freq(Hz: str):
        return Argument(f"@{Hz}")
    
    @staticmethod
    def gain(dB: str):
        return Argument(f"{dB}dB")
    
    @staticmethod
    def q(Q: str):
        return Argument(f"Q={Q}")
    
    @staticmethod
    def ms(ms: str):
        return Argument(f"{ms}ms")
    
    @staticmethod
    def samples(samples: str):
        return Argument(f"{samples}samples")
    
    @staticmethod
    def channel(channel: str):
        return Argument(f"{channel}")

        
class FilterArgsConsumer:
    def __init__(self, argStr: str):
        currentPart = ""
        nest = 0
        self._args = []

        for c in argStr:
            if c == '{':
                nest += 1
            if c == '}':
                nest -= 1
            
            if c == ',' and nest == 0:
                self._args.append(Argument(currentPart.strip()))
                currentPart = ""
                continue
            currentPart += c
        
        if len(currentPart) > 0:
            self._args.append(Argument(currentPart.strip()))
    
    def _getArg(self, argType: ArgumentType) -> Argument | None:
        # Find and the first argument of the given type,
        # then the first raw argument,
        # then none.
        for arg in self._args:
            if arg.type == argType and not arg.consumed:
                return arg
        return None
    
    def _getArgOrRaw(self, argType: ArgumentType) -> Argument | None:
        # Find and the first argument of the given type,
        # then the first raw argument,
        # then none.
        for arg in self._args:
            if arg.type == argType and not arg.consumed:
                return arg
        for arg in self._args:
            if arg.type == ArgumentType.Raw and not arg.consumed:
                return arg
        return None
    
    def getFreq(self, default: str) -> Argument:
        arg = self._getArgOrRaw(ArgumentType.Frequency)
        if arg is None:
            return Argument.freq(default).consume()
        return arg.consume()
    
    def getQ(self, default: str) -> Argument:
        arg = self._getArgOrRaw(ArgumentType.Q)
        if arg is None:
            return Argument.q(default).consume()
        return arg.consume()
    
    def getGain(self, default: str) -> Argument:
        arg = self._getArgOrRaw(ArgumentType.Decibels)
        if arg is None:
            return Argument.gain(default).consume()
        return arg.consume()

    def getDelay(self, default: str) -> Argument:
        arg = self._getArgOrRaw(ArgumentType.Milliseconds)
        if arg is None:
            arg = self._getArg(ArgumentType.Samples)
        if arg is None:
            return Argument(default).consume()
        return arg.consume()
    
    def getChannel(self, default: str) -> Argument:
        arg = self._getArgOrRaw(ArgumentType.Channel)
        if arg is None:
            return Argument(default).consume()
        return arg.consume()
    
    def getFilter(self) -> Argument:
        arg = self._getArgOrRaw(ArgumentType.Filter)
        if arg is None:
            return Argument("{}").consume()
        return arg.consume()


def checkPrefix(s: str, prefixes: list[str] | str) -> bool:
    if isinstance(prefixes, str):
        prefixes = [prefixes]
    l = s.lower()
    for p in prefixes:
        if l.startswith(p.lower()):
            return True
    return False

def checkPrefixExact(s: str, prefixes: list[str] | str) -> bool:
    if isinstance(prefixes, str):
        prefixes = [prefixes]
    for p in prefixes:
        if s.startswith(p):
            return True
    return False

class CommentType(Enum):
    NoComment = 0
    Line = 1
    Block = 2

def stripComments(s: str) -> str:
    res = ""
    comment = CommentType.NoComment
    for i in range(len(s)):
        if comment == CommentType.NoComment:
            if i < len(s) - 1 and s[i] == '/' and s[i+1] == '/':
                comment = CommentType.Line
                continue
            if i < len(s) - 1 and s[i] == '/' and s[i+1] == '*':
                comment = CommentType.Block
                continue
            res += s[i]
        elif comment == CommentType.Line:
            if s[i] == '\n':
                comment = CommentType.NoComment
                continue
        elif comment == CommentType.Block:
            if s[i] == '/' and s[i-1] == '*':
                comment = CommentType.NoComment
                continue
    return res

def parseEqFile(path: str, visitedPaths: list[str] | None = None) -> EqFile:
    """Parses an eq file and returns an EqFile object"""
    if visitedPaths is None:
        visitedPaths = []
    
    name = os.path.basename(path)
    name = name.split(".")[0]
    eqFile = EqFile(name)

    if path in visitedPaths or not os.path.exists(path):
        return eqFile

    visitedPaths.append(path)

    with open(path, "r") as f:
        raw = f.read()
    lines = stripComments(raw).split('\n')
    
    group: EqFilterGroup = EqFilterGroup()
    eqFile.groups.append(group)

    for line in lines:
        indent = 0
        for c in line:
            if c == ' ':
                indent += 1
            else:
                break
        line = line.strip()

        if line.startswith("#"):
            # directive
            spaceIdx = line.find(' ')
            directive = line[1:spaceIdx].strip()
            args = line[spaceIdx+1:].strip()
            eqFile.addDirective(directive, args)
            continue

        if line.endswith(":"):
            # begin new group
            channels = line[:-1].split()
            group = EqFilterGroup(0, channels)
            eqFile.groups.append(group)
            continue

        if indent < group.filters.indent:
            # end of group, begin new 'all' group
            group = EqFilterGroup(indent)
            eqFile.groups.append(group)
            indent = 0
            continue

        if len(group.filters) == 0:
            # first line after group directive decides the indent
            group.filters.indent = indent


        if not line.endswith(";"):
            continue
        line = line[:-1]

        isPanning = False
        panPosChannel = "R"
        panNegChannel = "L"

        filter: EqFilter | None = None

        if checkPrefix(line, "pan"):
            # ex: pan R, L, { PK @4300, 3dB, 3.8 };
            # the channel names are optional, defaults to R, L
            args = FilterArgsConsumer(line[4:])
            panPosChannel = args.getChannel('R').value
            panNegChannel = args.getChannel('L').value
            line = args.getFilter().value
            isPanning = True
        
        if checkPrefix(line, "include"):
            # ex: include example.txt
            inclpath = line[7:].strip()
            subEqFile = parseEqFile(os.path.join(os.path.dirname(path), inclpath), visitedPaths)
            currentChannels = group.channels
            for subEqFileGroup in subEqFile.groups:
                subEqFileGroup.filters.indent += group.filters.indent
                eqFile.groups.append(subEqFileGroup)
            group = EqFilterGroup(indent, currentChannels)
            eqFile.groups.append(group)
            continue

        elif checkPrefix(line, "gain"):
            # ex: gain -6dB
            args = FilterArgsConsumer(line[4:])
            parts = line.split()
            gain = args.getGain('0')
            filter = PreampFilter(
                gain.value
            )
        
        elif checkPrefix(line, "delay"):
            # ex: delay 50ms
            # ex: delay 480samples
            parts = line.split()
            delay = parts[1]
            useSamples = delay.endswith("samples")
            delay = delay[:-len("samples")] if useSamples else delay
            filter = DelayFilter(delay, useSamples)

        elif checkPrefixExact(line, ["L=", "R=", "C=", "LFE=", "RL=", "RR=", "SL=", "SR="]):
            # ex: L=L+0.5*R
            # ex: L=R+-6dB*C
            # ex: 1=R R=0.5
            # ex: LFE=L L=0.0 R=0.0 C=0.0 RL=0.0 RR=0.0
            copyExpressionsRaw = line.split(',')
            copyExpressions = []
            for copyExpressionRaw in copyExpressionsRaw:
                copyExpression = copyExpressionRaw.replace(' ', '')
                copyExpressions.append(copyExpression)
            filter = CopyFilter(copyExpressions)
        
        elif checkPrefix(line, "PK"):
            # ex: PK @4300, -3dB, 3.8
            args = FilterArgsConsumer(line[2:])
            freq = args.getFreq('100')
            gain = args.getGain('0')
            q = args.getQ('3')
            filter = PeakingFilter(
                gain.value,
                freq.value,
                q.value
            )

        elif checkPrefix(line,  ["HP", "LP", "BP"]):
            # ex: HP @291.76, 0.9012
            kind = (
                PassKind.High if line.startswith("HP") else
                PassKind.Low if line.startswith("LP") else
                PassKind.Band
            )
            args = FilterArgsConsumer(line[2:])
            freq = args.getFreq('1000')
            q = args.getQ('0.9012')
            filter = PassFilter(
                freq.value,
                kind,
                q.value
            )

        elif checkPrefix(line, ["HS", "LS", "BS"]):
            # ex: HS @91.82, 4.7, 1.0492
            kind = (
                PassKind.High if line.startswith("HS") else
                PassKind.Low if line.startswith("LS") else
                PassKind.Band
            )
            args = FilterArgsConsumer(line[2:])
            freq = args.getFreq('1000')
            gain = args.getGain('0')
            q = args.getQ('1.0492')
            filter = ShelfFilter(
                gain.value,
                freq.value,
                kind,
                q.value
            )
        
        elif checkPrefix(line, "AP"):
            # ex: AP @224.4, 8.0328
            args = FilterArgsConsumer(line[2:])
            freq = args.getFreq('100')
            q = args.getQ('8.0328')
            filter = AllPassFilter(
                freq.value,
                q.value
            )
        
        if filter is None:
            continue

        if isPanning:
            filter = PanningFilter(filter, panPosChannel, panNegChannel, group.channels)
        group.filters.addFilter(filter)


    
    # merge groups with same channels to reduce filter count
    newGroups = []
    
    for g in eqFile.groups:
        if len(newGroups) == 0:
            newGroups.append(g)
            continue
    
        lastGroup = newGroups[-1]
        lastChannels = lastGroup.channels
        currentChannels = g.channels
        isSameChannels = True
        if len(lastChannels) == len(currentChannels):
            for i in range(len(lastChannels)):
                if lastChannels[i] != currentChannels[i]:
                    isSameChannels = False
                    break
        
        if isSameChannels:
            for f in g.filters:
                lastGroup.filters.addFilter(f)
        else:
            newGroups.append(g)

    eqFile.groups = newGroups
    return eqFile

def compile(path: str):
    eqFile = parseEqFile(path)

    if "nocompile" in eqFile.getDirective("pragma"):
        return

    apoConfig = eqFile.toEqApo()

    lines = apoConfig.split('\n')
    # consecutive channel lines are redundant as only the last one is used
    newLines = []
    for line in lines:
        lastIsChannel = False
        if len(newLines) > 0 and newLines[-1].strip().startswith("Channel:"):
            lastIsChannel = True
        if lastIsChannel and line.startswith("Channel:"):
            newLines[-1] = line
        else:
            newLines.append(line)
    apoConfig = '\n'.join(newLines)

    os.makedirs(EQAPO_DIR, exist_ok=True)
    with open(os.path.join(EQAPO_DIR, eqFile.name + ".txt"), "w") as f:
        f.write(apoConfig)

def compileAll():
    paths = glob.glob(EQ_PATH + "*.eq")
    for path in paths:
        print(f"Compiling {path}")
        compile(path)
        time.sleep(0.1)

class EqFileWatcher(watchdog.events.FileSystemEventHandler):
    def __init__(self):
        self.observer = watchdog.observers.Observer()
        self.observer.schedule(self, path=EQ_PATH, recursive=True)
        self.observer.start()

    def on_any_event(self, event):
        if event.is_directory:
            return
        if event.event_type == 'modified':
            compileAll()

if __name__ == "__main__":
    compileAll()
    watcher = EqFileWatcher()
    while True:
        time.sleep(1)