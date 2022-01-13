import sys
import struct
from nml import lz77

import grf


def hex_str(s, n=None):
    add = ''
    if n is not None and len(s) > n:
        s = s[:n - 3]
        add = '...'
    if isinstance(s, (bytes, memoryview)):
        return ':'.join('{:02x}'.format(b) for b in s) + add
    return ':'.join('{:02x}'.format(ord(c)) for c in s) + add


def read_extended_byte(data, offset):
    res = data[offset]
    if res != 0xff:
        return res, offset + 1
    return data[offset + 1] | (data[offset + 2] << 8), offset + 3


def read_dword(data, offset):
    return data[offset] | (data[offset + 1] << 8) | (data[offset + 2] << 16) | (data[offset + 3] << 24), offset + 4


FEATURES = {
    0: 'Train',
    1: 'RV',
    2: 'Ship',
    3: 'Aircraft',
    4: 'Station',
    5: 'Canal',
    6: 'Bridge',
    7: 'House',
    8: 'Setting',
    9: 'IndTiles',
    0xa: 'Industry',
    0xb: 'Cargo',
    0xc: 'Sound',
    0xd: 'Airport',
    0xe: '?Signals?',
    0xf: 'Object',
    0x10: 'Railtype',
    0x11: 'AirportTiles',
    0x12: 'Roadtype',
    0x13: 'Tramtype',
}


def str_feature(feature):
    return f'{FEATURES[feature]}<{feature:02x}>'


def str_sprite(sprite):
    sprite_id = sprite & 0x1fff
    draw = {0: 'N', 1 : 'T', 2: 'R'}[(sprite >> 14) & 3]
    color_translation = (sprite >> 16) & 0x3fff
    normal_in_transparent = bool(sprite & (1 << 30))
    sprite_type = sprite >> 31
    ntstr = ['', ' NF'][normal_in_transparent]
    return f'[{sprite_id} {draw}{ntstr} {color_translation}-{sprite_type}]'


def read_property(data, ofs, fmt):
    if fmt == 'B':
        return data[ofs], ofs + 1

    if fmt == 'W':
        return data[ofs] | (data[ofs + 1] << 8), ofs + 2

    if fmt == 'L':
        return data[ofs: ofs + 4], ofs + 4

    if fmt == 'D':
        return read_dword(data, ofs)

    if fmt == 'B*':
        return read_extended_byte(data, ofs)

    if fmt == 'n*B':
        n = data[ofs]
        return data[ofs + 1: ofs + 1 + n], ofs + 1 + n

    if fmt == '(BV)+':
        res = {}
        while data[ofs] != 0:
            name_end = data.find(b'\0', ofs + 1)
            res[data[ofs]] = data[ofs + 1: name_end]
            ofs = name_end + 1
        return res, ofs + 1

    assert False, fmt


def decode_action0(data):
    feature = data[0]
    num_props = data[1]
    num_info = data[2]
    first_id, ofs = read_extended_byte(data, 3)
    props = {}
    for _ in range(num_props):
        prop = data[ofs]
        ofs += 1
        propdict = grf.ACTION0_PROPS[feature]
        name, fmt = propdict[prop]
        res = []
        for _ in range(num_info):
            value, ofs = read_property(data, ofs, fmt)
            res.append(value)
        # key = f'{name}<{prop:02x}>'
        # assert key not in props, key
        props[name] = res

    return [grf.Action0(
        feature=feature,
        first_id=first_id,
        count=num_info,
        props=props,
    )]


def decode_action1(data):
    sprite_count, _ = read_extended_byte(data, 2)
    return [grf.Action1(
        feature=data[0],
        set_count=data[1],
        sprite_count=sprite_count
    )]


SPRITE_GROUP_OP = [
    'ADD',   # a + b
    'SUB',   # a - b
    'SMIN',  # (signed) min(a, b)
    'SMAX',  # (signed) max(a, b)
    'UMIN',  # (unsigned) min(a, b)
    'UMAX',  # (unsigned) max(a, b)
    'SDIV',  # (signed) a / b
    'SMOD',  # (signed) a % b
    'UDIV',  # (unsigned) a / b
    'UMOD',  # (unsigned) a & b
    'MUL',   # a * b
    'AND',   # a & b
    'OR',    # a | b
    'XOR',   # a ^ b
    'STO',   # store a into temporary storage, indexed by b. return a
    'RST',   # return b
    'STOP',  # store a into persistent storage, indexed by b, return a
    'ROR',   # rotate a b positions to the right
    'SCMP',  # (signed) comparison (a < b -> 0, a == b = 1, a > b = 2)
    'UCMP',  # (unsigned) comparison (a < b -> 0, a == b = 1, a > b = 2)
    'SHL',   # a << b
    'SHR',   # (unsigned) a >> b
    'SAR',   # (signed) a >> b
]

class DataReader:
    def __init__(self, data, offset=0):
        self.data = data
        self.offset = offset

    def get_byte(self):
        self.offset += 1
        return self.data[self.offset - 1]

    def get_extended_byte(self):
        res, self.offset = read_extended_byte(self.data, self.offset)
        return res

    def get_word(self):
        return self.get_byte() | (self.get_byte() << 8)

    def get_var(self, n):
        size = 1 << n
        res = struct.unpack_from({0: '<B', 1: '<H', 2: '<I'}[n], self.data, offset=self.offset)[0]
        self.offset += size
        return res

    def hex_str(self, n):
        return hex_str(self.data[self.offset: self.offset + n])


TLF_NOTHING           = 0x00
TLF_DODRAW            = 0x01  # Only draw sprite if value of register TileLayoutRegisters::dodraw is non-zero.
TLF_SPRITE            = 0x02  # Add signed offset to sprite from register TileLayoutRegisters::sprite.
TLF_PALETTE           = 0x04  # Add signed offset to palette from register TileLayoutRegisters::palette.
TLF_CUSTOM_PALETTE    = 0x08  # Palette is from Action 1 (moved to SPRITE_MODIFIER_CUSTOM_SPRITE in palette during loading).
TLF_BB_XY_OFFSET      = 0x10  # Add signed offset to bounding box X and Y positions from register TileLayoutRegisters::delta.parent[0..1].
TLF_BB_Z_OFFSET       = 0x20  # Add signed offset to bounding box Z positions from register TileLayoutRegisters::delta.parent[2].
TLF_CHILD_X_OFFSET    = 0x10  # Add signed offset to child sprite X positions from register TileLayoutRegisters::delta.child[0].
TLF_CHILD_Y_OFFSET    = 0x20  # Add signed offset to child sprite Y positions from register TileLayoutRegisters::delta.child[1].
TLF_SPRITE_VAR10      = 0x40  # Resolve sprite with a specific value in variable 10.
TLF_PALETTE_VAR10     = 0x80  # Resolve palette with a specific value in variable 10.
TLF_KNOWN_FLAGS       = 0xFF  # Known flags. Any unknown set flag will disable the GRF.

# /** Flags which are still required after loading the GRF. */
TLF_DRAWING_FLAGS     = ~TLF_CUSTOM_PALETTE

# /** Flags which do not work for the (first) ground sprite. */
TLF_NON_GROUND_FLAGS  = TLF_BB_XY_OFFSET | TLF_BB_Z_OFFSET | TLF_CHILD_X_OFFSET | TLF_CHILD_Y_OFFSET

# /** Flags which refer to using multiple action-1-2-3 chains. */
TLF_VAR10_FLAGS       = TLF_SPRITE_VAR10 | TLF_PALETTE_VAR10

# /** Flags which require resolving the action-1-2-3 chain for the sprite, even if it is no action-1 sprite. */
TLF_SPRITE_REG_FLAGS  = TLF_DODRAW | TLF_SPRITE | TLF_BB_XY_OFFSET | TLF_BB_Z_OFFSET | TLF_CHILD_X_OFFSET | TLF_CHILD_Y_OFFSET

# /** Flags which require resolving the action-1-2-3 chain for the palette, even if it is no action-1 palette. */
TLF_PALETTE_REG_FLAGS = TLF_PALETTE


def read_sprite_layout_registers(d, flags, is_parent):
    # regs = {'flags': flags & TLF_DRAWING_FLAGS}
    regs = {}
    if flags & TLF_DODRAW:  regs['dodraw']  = d.get_byte();
    if flags & TLF_SPRITE:  regs['add']  = grf.Temp(d.get_byte());
    if flags & TLF_PALETTE: regs['palette'] = d.get_byte();

    if is_parent:
        delta = [d.get_byte(), d.get_byte(), 0] if flags & TLF_BB_XY_OFFSET else [0, 0, 0]
        if flags & TLF_BB_Z_OFFSET: delta[2] = d.get_byte()
        regs['delta_parent'] = tuple(delta)
    else:
        delta, delta_set = [0, 0], False
        if flags & TLF_CHILD_X_OFFSET: delta[0], delta_set = d.get_byte(), True
        if flags & TLF_CHILD_Y_OFFSET: delta[1], delta_set = d.get_byte(), True
        if delta_set: regs['delta_child'] = tuple(delta)

    if flags & TLF_SPRITE_VAR10: regs['sprite_var10'] = d.get_byte()
    if flags & TLF_PALETTE_VAR10: regs['palette_var10'] = d.get_byte()
    return regs


def read_sprite_layout(d, feature, ref_id, num, basic_format):
    assert feature in (0x07, 0x09, grf.OBJECT, 0x11), feature

    has_z_position = not basic_format
    has_flags = bool((num >> 6) & 1)
    num &= 0x3f

    def read_sprite():
        sprite = d.get_word()
        pal = d.get_word()
        flags = d.get_word() if has_flags else TLF_NOTHING
        return {'sprite': grf.Sprite.from_grf(sprite, pal), 'flags': flags}

    ground = read_sprite()
    ground_regs = read_sprite_layout_registers(d, ground['flags'], False)
    sprites = []
    for _ in range(num):
        seq = read_sprite()
        delta = seq['offset'] = (d.get_byte(), d.get_byte(), d.get_byte() if has_z_position else 0)
        is_parent = (delta[2] != 0x80)
        if is_parent:
            seq['extent'] = (d.get_byte(), d.get_byte(), d.get_byte())
        seq['regs'] = read_sprite_layout_registers(d, seq['flags'] & TLF_DRAWING_FLAGS, is_parent)
        sprites.append(seq)

    if basic_format:
        assert len(sprites) == 1, len(sprites)
        return [grf.BasicSpriteLayout(
            feature=feature,
            ref_id=ref_id,
            ground_sprite=ground,
            building_sprite=sprites[0],
        )]

    return [[grf.ExtendedSpriteLayout, grf.AdvancedSpriteLayout][has_flags](
        feature=feature,
        ref_id=ref_id,
        ground=ground,
        buildings=sprites,
    )]


VA2_GLOBALS = {
    0x00: ('date', 'W'),  # 80      W   current date (counted as days from 1920)[1]
    0x01: ('year', 'B'),  # 81      B   ￼0.6 ￼2.0   Current year (count from 1920, max. 2175 even with eternalgame)[1]
    0x02: ('month', 'B/D'),  # 82      B/D ￼0.6 ￼2.0   current month (0-11) in bits 0-7; the higher bytes contain unusable junk.[1] ￼0.7 ￼ Since OpenTTD r13594 'day of month' (0-30) is stored in bits 8-12, bit 15 is set in leapyears and 'day of year'(0-364 resp. 365) is stored in bits 16-24. All other bits are reserved and should be masked.
    0x03: ('climate', 'B'),  # 83      B   ￼0.6 ￼2.0   Current climate: 00 = temp, 01 = arctic, 02 = trop, 03 = toyland
                      # 84      D   ￼0.6 ￼2.0   GRF loading stage, see below
                      # 85      B   ￼0.6 ￼2.0   TTDPatch flags: only for bit tests
    0x06: ('drive_side', 'B'),  # 86      B   ￼0.6 ￼2.0   Road traffic side: bit 4 clear=left, set=right; other bits are reserved and must be masked. (87)    (87)    B   ￼ ￼ No longer used since TTDPatch 2.0. (was width of "€" character)
                      # 88      4*B   ￼0.6 ￼2.0   Checks specified GRFID (see condition-types)[2]
    0x09: ('date_fract', 'W'),  # 89      W   ￼0.6 ￼2.0   date fraction, incremented by 0x375 every engine tick
    0x0A: ('anim_counter', 'W'),  # 8A      W   ￼0.6 ￼2.0   animation counter, incremented every tick
    0x0B: ('ttdp_version', 'D'),  # 8B      D   ￼ ￼2.0  TTDPatch version, see below [3][4]
    0x0C: ('cur_cb_id', 'W'),  #         W   ￼0.6 ￼2.5   current callback ID (feature-specific), set to 00 when not in a callback
    0x0D: ('ttd_version', 'B'),  # 8D      B   ￼0.6 ￼2.5   TTD version, 0=DOS, 1=Windows
    0x0E: ('train_y_ofs', 'B'),  # 8E  8E  B   ￼0.6 ￼2.5   Y-Offset for train sprites
    0x0F: ('rail_cost', '3*B'),  # 8F  8F  3*B ￼0.6 ￼2.5   Rail track type cost factors
    0x10: ('cb_info1', 'D'),  #         D   ￼0.6 ￼2.5   Extra callback info 1, see below.
    0x11: ('cur_rail_tool', 'B'),  #         B   ￼ ￼2.5  current rail tool type (for station callbacks)
    0x12: ('game_mode', 'B'),  # 92      B   ￼0.6 ￼2.5   Game mode, 0 in title screen, 1 in game and 2 in editor
    0x13: ('tile_refresh_left', 'W'),  # 93  93  W   ￼ ￼2.5  Tile refresh offset to left [5]
    0x14: ('tile_refresh_right', 'W'),  # 94  94  W   ￼ ￼2.5  Tile refresh offset to right [5]
    0x15: ('tile_refresh_up', 'W'),  # 95  95  W   ￼ ￼2.5  Tile refresh offset upwards [5]
    0x16: ('tile_refresh_down', 'W'),  # 96  96  W   ￼ ￼2.5  Tile refresh offset downwards [5]
                      # 97  97  B   ￼ ￼2.5  Fixed snow line height [6][7]
    0x18: ('cb_info2', 'D'),  #         D   ￼0.6 ￼2.5   Extra callback info 2, see below.
                      # 99  99  D   ￼ ￼2.5  Global ID offset
    0x1A: ('max_uint32', 'D'),  # 9A      D   ￼0.6 ￼2.5   Has always all bits set; you can use this to make unconditional jumps
    0x1B: ('display_options', 'B'),  #         B   ￼ ￼2.5  display options; bit 0=town names, 1=station names, 2=signs, 3=animation, 4=transparency, 5=full detail
    0x1C: ('va2_ret', 'D'),  #         D   ￼0.6 ￼2.5   result from most recent VarAction2
    0x1D: ('ttd_platform', 'D'),  # 9D      D   ￼0.6 ￼2.5   TTD Platform, 0=TTDPatch, 1=OpenTTD [4]
    0x1E: ('grf_featuers', 'D'),  # 9E  9E  D   ￼0.6 ￼2.5   Misc. GRF Features
                      # 9F  D   ￼ ￼2.5  writable only: Locale-dependent settings
    0x20: ('snow_line', 'B'),  #         B   ￼0.6 ￼2.5   Current snow line height, FFh if snow isn't present at all [7]
    0x21: ('openttd_version', 'D'),  # A1      D   ￼0.6 ￼  OpenTTD version, see below. [4]
    0x22: ('difficulty_level', 'D'),  # A2      D   ￼0.7 ￼2.6   Difficulty level: 00= easy, 01=medium, 02=hard, 03=custom
    0x23: ('date_long', 'D'),  # A3      D   ￼0.7 ￼2.6   Current date long format
    0x24: ('year_zero', 'D'),  # A4      D   ￼0.7 ￼2.6   Current year zero based
    0x25: ('a3_grfid', 'D'),  #         D   ￼0.7 ￼  GRFID of the grf that contains the corresponding Action3. Useful when accessing the "related" object. Currently only supported for vehicles.
}

V2_OBJECT_VARS = {
    0x40: ('relative_pos', 'D'),  # Relative position, like Industry Tile var43
    0x41: ('tile_info', 'W'),  # Tile information, see below
    0x42: ('constructed', 'D'),  # Construction date from year 0
    0x43: ('anim_counter', 'B'),  # Animation counter, see below
    0x44: ('founder', 'B'),  # Object founder information
    0x45: ('closest_town_info', 'D'),  # Get town zone and Manhattan distance of closest town
    0x46: ('closest_town_dist_squared', 'D'),  # Get square of Euclidian distance of closest town
    0x47: ('colour', 'B'),  # Object colour
    0x48: ('views', 'B'),  # Object views
    0x60: ('type_view_ofs', 'W'),  # Get object type and view at offset
    0x61: ('random_ofs', 'B'),  # Get random bits at offset
    0x62: ('nearby_tile_info', 'D'),  # Land info of nearby tiles
    0x63: ('nearby_anim_counter', 'W'),  # Animation counter of nearby tile
    0x64: ('object_count', 'D'),  # Count of object, distance of closest instance
}

VA2_OP = {
    0x00: '+',  # \2+ result = val1 + val2    Supported by OpenTTD 0.60.6 Supported by TTDPatch 2.52.5
    0x01: '-',  # \2- result = val1 - val2    Supported by OpenTTD 0.60.6 Supported by TTDPatch 2.52.5
    0x02: 'min',  # \2< result = min(val1, val2)    Supported by OpenTTD 0.60.6 Supported by TTDPatch 2.52.5    val1 and val2 are both considered signed
    0x03: 'max',  # \2> result = max(val1, val2)    Supported by OpenTTD 0.60.6 Supported by TTDPatch 2.52.5
    0x04: 'min',  # \2u<    result = min(val1, val2)    Supported by OpenTTD 0.60.6 Supported by TTDPatch 2.52.5    val1 and val2 are both considered unsigned
    0x05: 'max',  # \2u>    result = max(val1, val2)    Supported by OpenTTD 0.60.6 Supported by TTDPatch 2.52.5
    0x06: '/',  #  \2/ result = val1 / val2    Supported by OpenTTD 0.60.6 Supported by TTDPatch 2.52.5    val1 and val2 are both considered signed
    0x07: '%',  #  \2% result = val1 mod val2  Supported by OpenTTD 0.60.6 Supported by TTDPatch 2.52.5
    0x08: 'u/',  #  \2u/    result = val1 / val2    Supported by OpenTTD 0.60.6 Supported by TTDPatch 2.52.5    val1 and val2 are both considered unsigned
    0x09: 'u%',  #  \2u%    result = val1 mod val2  Supported by OpenTTD 0.60.6 Supported by TTDPatch 2.52.5
    0x0A: '*',  #   \2* result = val1 * val2    Supported by OpenTTD 0.60.6 Supported by TTDPatch 2.52.5    result will be truncated to B/W/D (that makes it the same for signed/unsigned operands)
    0x0B: '&',  #   \2& result = val1 & val2    Supported by OpenTTD 0.60.6 Supported by TTDPatch 2.52.5    bitwise AND
    0x0C: '|',  #   \2| result = val1 | val2    Supported by OpenTTD 0.60.6 Supported by TTDPatch 2.52.5    bitwise OR
    0x0D: '^',  #   \2^ result = val1 ^ val2    Supported by OpenTTD 0.60.6 Supported by TTDPatch 2.52.5    bitwise XOR
    0x0E: '(tsto)',  #  \2s or \2sto [1]    var7D[val2] = val1, result = val1   Supported by OpenTTD 0.60.6 Supported by TTDPatch 2.6 (r1246)2.6    Store result. See Temporary storage.
    0x0F: ';',  #   \2r or \2rst [1]    result = val2 [2]   Supported by OpenTTD 0.60.6 Supported by TTDPatch 2.6 (r1246)2.6
    0x10: '(psto)',  #  \2psto [3]  var7C[val2] = val1, result = val1   Supported by OpenTTD 0.60.6 Supported by TTDPatch 2.6 (r1315)2.6    Store result into persistent storage. See Persistent storage.
    0x11: '(ror)',  #  \2ror or \2rot [4]  result = val1 rotate right val2 Supported by OpenTTD 0.60.6 Supported by TTDPatch 2.6 (r1651)2.6    Always a 32-bit rotation.
    0x12: '(cmp)',  #  \2cmp [3]   see notes   Supported by OpenTTD 0.60.6 Supported by TTDPatch 2.6 (r1698)2.6    Result is 0 if val1<val2, 1 if val1=val2 and 2 if val1>val2. Both values are considered signed. [5]
    0x13: '(ucmp)',  #  \2ucmp [3]  see notes   Supported by OpenTTD 0.60.6 Supported by TTDPatch 2.6 (r1698)2.6    The same as 12, but operands are considered unsigned. [5]
    0x14: '<<',  #  \2<< [3]    result = val1 << val2   Supported by OpenTTD 1.1 (r20332)1.1 Supported by TTDPatch 2.6 (r2335)2.6   shift left; val2 should be in the range 0 to 31.
    0x15: 'u>>',  # \2u>> [3]   result = val1 >> val2   Supported by OpenTTD 1.1 (r20332)1.1 Supported by TTDPatch 2.6 (r2335)2.6   shift right (unsigned); val2 should be in the range 0 to 31.
    0x16: '>>',  #  \2>> [3]    result = val1 >> val2   Supported by OpenTTD 1.1 (r20332)1.1 Supported by TTDPatch 2.6 (r2335)2.6   shift right (signed); val2 should be in the range 0 to 31.
}


def get_va2_var(var):
    if var < 0x40:
        name, fmt = VA2_GLOBALS[var]
        return f'[{name}]', fmt
    if var == 0x5f: return '(random)', 'D'
    if var == 0x7b: return '(var_eval)', ''
    if var == 0x7c: return '(perm)', 'D'
    if var == 0x7d: return '(temp)', 'D'
    if var == 0x7e: return '(call)', 'D'
    if var == 0x7f: return '(param)', 'D'
    return V2_OBJECT_VARS[var]


class Generic(grf.Node):
    def __init__(self, var, shift, and_mask, type, add_val, divmod_val):
        self.var = var
        self.shift = shift
        self.and_mask = and_mask
        self.type = type
        self.add_val = add_val
        self.divmod_val = divmod_val

    def format(self, parent_priority=0):
        addstr = ''
        if self.type == 1:
            addstr = f' +{self.add_val} /{self.divmod_val}'
        elif self.type == 2:
            addstr = f' +{self.add_val} %{self.divmod_val}'
        return [f'(var{self.var:02x} >>{self.shift} &{self.and_mask:x}{addstr})']


signed_tile_offset = None
industry_count = None

NML_VARACT2_GLOBALVARS = {
    'current_month'        : {'var': 0x02, 'start':  0, 'size':  8},
    'current_day_of_month' : {'var': 0x02, 'start':  8, 'size':  5},
    'is_leapyear'          : {'var': 0x02, 'start': 15, 'size':  1},
    'current_day_of_year'  : {'var': 0x02, 'start': 16, 'size':  9},
    'traffic_side'         : {'var': 0x06, 'start':  4, 'size':  1},
    'animation_counter'    : {'var': 0x0A, 'start':  0, 'size': 16},
    'current_callback'     : {'var': 0x0C, 'start':  0, 'size': 16},
    'extra_callback_info1' : {'var': 0x10, 'start':  0, 'size': 32},
    'game_mode'            : {'var': 0x12, 'start':  0, 'size':  8},
    'extra_callback_info2' : {'var': 0x18, 'start':  0, 'size': 32},
    'display_options'      : {'var': 0x1B, 'start':  0, 'size':  6},
    'last_computed_result' : {'var': 0x1C, 'start':  0, 'size': 32},
    'snowline_height'      : {'var': 0x20, 'start':  0, 'size':  8},
    'difficulty_level'     : {'var': 0x22, 'start':  0, 'size':  8},
    'current_date'         : {'var': 0x23, 'start':  0, 'size': 32},
    'current_year'         : {'var': 0x24, 'start':  0, 'size': 32},

    # TODO object vars
    'relative_x'             : {'var': 0x40, 'start':  0, 'size':  8},
    'relative_y'             : {'var': 0x40, 'start':  8, 'size':  8},
    'relative_pos'           : {'var': 0x40, 'start':  0, 'size': 16},

    'terrain_type'           : {'var': 0x41, 'start':  0, 'size':  3},
    'tile_slope'             : {'var': 0x41, 'start':  8, 'size':  5},

    'build_date'             : {'var': 0x42, 'start':  0, 'size': 32},

    'animation_frame'        : {'var': 0x43, 'start':  0, 'size':  8},
    'company_colour'         : {'var': 0x43, 'start':  0, 'size':  8},

    'owner'                  : {'var': 0x44, 'start':  0, 'size':  8},

    'town_manhattan_dist'    : {'var': 0x45, 'start':  0, 'size': 16},
    'town_zone'              : {'var': 0x45, 'start': 16, 'size':  8},

    'town_euclidean_dist'    : {'var': 0x46, 'start':  0, 'size': 16},
    'view'                   : {'var': 0x48, 'start':  0, 'size':  8},
    'random_bits'            : {'var': 0x5F, 'start':  8, 'size':  8},


    'tile_height' : {'var': 0x62, 'start':  16, 'size':  8},

    'nearby_tile_object_type'      : {'var': 0x60, 'start':  0, 'size': 16, 'param_function': signed_tile_offset},
    'nearby_tile_object_view'      : {'var': 0x60, 'start': 16, 'size':  4, 'param_function': signed_tile_offset},

    'nearby_tile_random_bits'      : {'var': 0x61, 'start':  0, 'size':  8, 'param_function': signed_tile_offset},

    'nearby_tile_slope'            : {'var': 0x62, 'start':  0, 'size':  5, 'param_function': signed_tile_offset},
    'nearby_tile_is_same_object'   : {'var': 0x62, 'start':  8, 'size':  1, 'param_function': signed_tile_offset},
    'nearby_tile_is_water'         : {'var': 0x62, 'start':  9, 'size':  1, 'param_function': signed_tile_offset},
    'nearby_tile_terrain_type'     : {'var': 0x62, 'start': 10, 'size':  3, 'param_function': signed_tile_offset},
    'nearby_tile_water_class'      : {'var': 0x62, 'start': 13, 'size':  2, 'param_function': signed_tile_offset},
    'nearby_tile_height'           : {'var': 0x62, 'start': 16, 'size':  8, 'param_function': signed_tile_offset},
    'nearby_tile_class'            : {'var': 0x62, 'start': 24, 'size':  4, 'param_function': signed_tile_offset},

    'nearby_tile_animation_frame'  : {'var': 0x63, 'start':  0, 'size':  8, 'param_function': signed_tile_offset},

    'object_count'                 : {'var': 0x64, 'start': 16, 'size':  8, 'param_function': industry_count},
    'object_distance'              : {'var': 0x64, 'start':  0, 'size': 16, 'param_function': industry_count},
}

NML_VARACT2_GLOBALVARS_INV = { (v['var'], v['start'], (1 << v['size']) - 1): k for k, v in NML_VARACT2_GLOBALVARS.items()}


def decode_action2(data):
    feature = data[0]
    ref_id = data[1]
    num_ent1 = data[2]
    d = DataReader(data, 3)

    # print(f'    <2>SPRITEGROUP feature:{str_feature(feature)} set_id:{set_id} ', end='')

    if feature in (0x07, 0x09, 0x0f, 0x11):
        if num_ent1 == 0:
            ground_sprite, building_sprite, xofs, yofs, xext, yext, zext = struct.unpack_from('<IIBBBBB', data, offset=3)
            ground_sprite = str_sprite(ground_sprite)
            building_sprite = str_sprite(building_sprite)
            # print(f'BASIC ground_sprite:{ground_sprite} building_sprite:{building_sprite} '
            #       f'xofs:{xofs} yofs:{yofs} extent:({xext}, {yext}, {zext})')
            raise NotImplementedError

        if num_ent1 <= 0x3f:
            raise NotImplemented

        if num_ent1 in (0x81, 0x82, 0x85, 0x86, 0x89, 0x8a):
            # varact2
            group_size = (num_ent1 >> 2) & 3
            related_scope = bool(num_ent1 & 2)
            first = True
            ofs = 3
            root = None
            while True:
                op = 0 if first else d.get_byte()
                var = d.get_byte()
                if 0x60 <= var < 0x80:
                    param = d.get_byte()
                varadj = d.get_byte()
                shift = varadj & 0x1f
                has_more = bool(varadj & 0x20)
                node_type = varadj >> 6
                and_mask = d.get_var(group_size)
                if node_type != 0:
                    # old magic, use advaction2 instead
                    add_val = d.get_var(group_size)
                    divmod_val = d.get_var(group_size)
                    node = Generic(var, shift, and_mask, node_type, add_val, divmod_val)
                elif var == 0x1a and shift == 0:
                    node = grf.Value(and_mask)
                elif (var, shift, and_mask) == (0x7c, 0, 0xffffffff):
                    node = grf.Perm(param)
                elif (var, shift, and_mask) == (0x7d, 0, 0xffffffff):
                    node = grf.Temp(param)
                elif (var, shift, and_mask) == (0x7e, 0, 0xffffffff):
                    node = grf.Call(param)
                else:
                    var_name = NML_VARACT2_GLOBALVARS_INV.get((var, shift, and_mask))
                    if var_name is not None:
                        node = grf.Var(var_name)
                    else:
                        node = Generic(var, shift, and_mask, 0, None, None)

                if first:
                    root = node
                else:
                    root = grf.Expr(op, root, node)

                first = False
                if not has_more:
                    break

            # no ranges is special for "do not switch, return the switch value"
            # <frosch123> oh, also, the ranges are unsigned
            # <frosch123> so if you want to set -5..5 you have to split into two ranges -5..-1, 0..5
            n_ranges = d.get_byte()
            ranges = []
            for _ in range(n_ranges):
                group = d.get_word()
                low = d.get_var(group_size)
                high = d.get_var(group_size)
                ranges.append(grf.Range(low, high, grf.Ref(group)))

            default_group = grf.Ref(d.get_word())

            # print(f'VARACT default_group:{default_group} related_scope:{related_scope} ranges:{ranges} ')
            # for a in adjusts:
            #     var = a['var']
            #     name, fmt = get_va2_var(var)
            #     op = VA2_OP[a['op']]
            #     param_str = ''
            #     if 0x60 <= var < 0x80:
            #         if var == 0x7e:
            #             param_str = ' proc:{:02x}'.format(a['subroutine'])
            #         else:
            #             param_str = ' param:{:02x}'.format(a['parameter'])
            #     type_str = ''
            #     if a['type'] != 0:
            #         type_str = '+{add_val} /%{divmod_val}'.format(**a)
            #     print(f'   op<{a["op"]}>:{op} var<{var:02x}>:{name}({fmt}){param_str} type:{a["type"]} >>{a["shift_num"]} &{a["and_mask"]:x}{type_str}')


            return [grf.VarAction2(
                feature=feature,
                ref_id=ref_id,
                related_scope=related_scope,
                ranges=ranges,
                default=default_group,
                code='\n'.join(root.format()),
            )]

        return read_sprite_layout(d, feature, ref_id, max(num_ent1, 1), num_ent1 == 0)
        # num_loaded = num_ent1
        # num_loading = get_byte()
        # [get_word() for i in range(num_loaded)]
        # [get_word() for i in range(num_loading)]
        # assert False, num_ent1
        # # assert num_ent1 < 0x3f + 0x40, num_ent1
        # return

    num_ent2 = data[3]
    ent1 = struct.unpack_from('<' + 'H' * num_ent1, data, offset=4)
    ent2 = struct.unpack_from('<' + 'H' * num_ent2, data, offset=4 + 2 * num_ent1)
    # print(f'ent1:{ent1} ent2:{ent2}')
    # return grf.BasicSpriteLayout(
    #     feature=feature,
    #     ref_id=ref_id,
    # )
    raise NotImplementedError


def decode_action3(data):
    feature = data[0]
    idcount = data[1]
    objs = []
    maps = []
    if data[1] == 0:
        _, default = struct.unpack_from('<BH', data, offset=2)
    else:
        d = DataReader(data, 2)
        objs = [d.get_byte() for _ in range(idcount)]
        cidcount = d.get_byte()
        for _ in range(cidcount):
            ctype = d.get_byte()
            groupid = grf.Ref(d.get_word())
            maps.append({'ctype': ctype, 'groupid': groupid})
        default = d.get_word()
        # print(f'objs:{objs} maps:{maps} default_gid:{def_gid}')
    return [grf.Action3(
        feature=feature,
        ids=objs,
        maps=maps,
        default=grf.Ref(default),
    )]


def decode_action4(data):
    fmt = '<BBB' + ('H' if data[1] & 0x80 else 'B')
    feature, lang, num, offset = struct.unpack_from(fmt, data)
    # strings = [s.decode('utf-8') for s in data[struct.calcsize(fmt):].split(b'\0')[:-1]]
    strings = [s for s in data[struct.calcsize(fmt):].split(b'\0')[:-1]]
    assert len(strings) == num, (len(strings), num,)
    # print(f'    <4>STRINGS feature:{str_feature(feature)} lang:{lang} num:{num} offset:{offset} strings:{strings}')
    return [grf.Action4(
        feature=feature,
        lang=lang & 0x7f,
        offset=offset,
        strings=strings,
    )]


def decode_action5(data):
    t = data[0]
    offset = None
    num, dataofs = read_extended_byte(data, 1)
    if t & 0xf0:
        offset, _ = read_extended_byte(data, dataofs)
        t &= ~0xf0
    return [grf.ReplaceNewSprites(t, num, offset=offset)]


def decode_action6(data):
    d = DataReader(data, 0)
    params = []
    while True:
        param_num = d.get_byte()
        if param_num == 0xFF:
            break
        param_size = d.get_byte()
        offset = d.get_extended_byte()
        params.append({'num': param_num, 'size': param_size, 'offset': offset})
    print(f'    <6>EDITPARAM params:{params}')
    # return []
    raise NotImplementedError


def decode_action7(data):
    variable = data[0]
    varsize = data[1]
    condition_type = data[2]
    value = 0
    for i in range(varsize):
        value |= data[3 + i] << (8 * i)
    skip_count = data[3 + varsize]
    params = []
    while True:
        param_num = d.get_byte()
        if param_num == 0xFF:
            break
        param_size = d.get_byte()
        offset = d.get_extended_byte()
        params.append({'num': param_num, 'size': param_size, 'offset': offset})
    print(f'    <6>EDITPARAM params:{params}')
    # return []
    raise NotImplementedError


def decode_action8(data):
    version = data[0]
    grfid = data[1: 5]
    pos = data.find(b'\0', 5)
    name = data[5: pos]
    description = data[pos + 1: -1]
    return [grf.SetDescription(
        version=version,
        grfid=grfid,
        name=name,
        description=description,
    )]


def decode_actionA(data):
    num = data[0]
    sets = [struct.unpack_from('<BH', data, offset=3*i + 1) for i in range(num)]
    return [grf.ReplaceOldSprites(sets)]


OPERATIONS = {
    0x00: '{target} = {source1}', # Supported by OpenTTD Supported by TTDPatch  Assignment  target = source1
    0x01: '{target} = {source1} + {source2}', # Supported by OpenTTD Supported by TTDPatch  Addition    target = source1 + source2
    0x02: '{target} = {source1} - {source2}', # Supported by OpenTTD Supported by TTDPatch  Subtraction     target = source1 - source2
    0x03: '{target} = {source1} * {source2} (Unsigned)', # Supported by OpenTTD Supported by TTDPatch  Unsigned multiplication     target = source1 * source2, with both sources being considered to be unsigned
    0x04: '{target} = {source1} * {source2} (Signed)', # Supported by OpenTTD Supported by TTDPatch  Signed multiplication   target = source1 * source2, with both sources considered signed
    0x05: '{target} = {source1} <</>> {source2} (Unsigned)', # Supported by OpenTTD Supported by TTDPatch  Unsigned bit shift  target = source1 << source2 if source2>0, or target = source1 >> abs(source2) if source2 < 0. source1 is considered to be unsigned
    0x06: '{target} = {source1} <</>> {source2} (Signed)', # Supported by OpenTTD Supported by TTDPatch  Signed bit shift    same as 05, but source1 is considered signed)
    0x07: '{target} = {source1} & {source2}', # Supported by OpenTTD Supported by TTDPatch 2.5 (alpha 48)2.5    Bitwise AND     target = source1 AND source2
    0x08: '{target} = {source1} | {source2}', # Suported by OpenTTD Supported by TTDPatch 2.5 (alpha 48)2.5    Bitwise OR  target = source1 OR source2
    0x09: '{target} = {source1} / {source2} (Unsigned)', # Supported by OpenTTD Supported by TTDPatch 2.5 (alpha 59)2.5    Unsigned division   target = source1 / source2
    0x0A: '{target} = {source1} / {source2} (Signed)', # Supported by OpenTTD Supported by TTDPatch 2.5 (alpha 59)2.5    Signed division     target = source1 / source2
    0x0B: '{target} = {source1} % {source2} (Unsigned)', # Supported by OpenTTD Supported by TTDPatch 2.5 (alpha 59)2.5    Unsigned modulo     target = source1 % source2
    0x0C: '{target} = {source1} % {source2} (Signed)', # Supported by OpenTTD Supported by TTDPatch 2.5 (alpha 59)2.5    Signed modulo   target = source1 % source2
}


def decode_actionC(data):
    return [grf.Comment(data)]


def decode_actionD(data):
    target = data[0]
    operation = data[1]
    source1 = data[2]
    source2 = data[3]
    if source1 == 0xff or source2 == 0xff:
        value, _ = read_dword(data, 4)
    fmt = OPERATIONS[operation]
    sf = lambda x: f'[{x:02x}]' if x != 0xff else str(value)
    target_str = f'[{target:02x}]'
    op_str = fmt.format(target=target_str, source1=sf(source1), source2=sf(source2))
    print(f'    <A>OP {op_str}')
    raise NotImplementedError


def decode_action14(data):
    res = {}
    ofs = 0

    def decode_chunk(res):
        nonlocal ofs
        chunk_type = data[ofs]
        ofs += 1
        if chunk_type == 0: return False
        chunk_id = data[ofs: ofs + 4]
        ofs += 4
        if chunk_type == b'C'[0]:
            res[chunk_id] = {}
            while decode_chunk(res[chunk_id]):
                pass
        elif chunk_type == b'B'[0]:
            l = data[ofs] | (data[ofs + 1] << 8)
            res[chunk_id] = data[ofs + 2: ofs + 2 + l]
            ofs += 2 + l
        elif chunk_type == b'T'[0]:
            lang = data[ofs]
            text_end = data.find(b'\0', ofs + 1)
            text = data[ofs + 1 : text_end]
            ofs = text_end + 1
        else:
            assert False, chunk_type
        return True

    while decode_chunk(res):
        pass

    return [grf.SetProperties(res)]


ACTIONS = {
    0x00: decode_action0,
    0x01: decode_action1,
    0x02: decode_action2,
    0x03: decode_action3,
    0x04: decode_action4,
    0x05: decode_action5,
    0x06: decode_action6,
    0x08: decode_action8,
    0x0a: decode_actionA,
    0x0c: decode_actionC,
    0x0d: decode_actionD,
    0x14: decode_action14,
}


class PyComment:
    def __init__(self, text):
        self.text = text

    def py(self):
        return f'# {self.text}'


def read_pseudo_sprite(f, nfo_line, container):
    data = f.read(2 if container == 1 else 4)
    if not data and container == 1:
        return False, []
    # print(hex_str(data))
    l = struct.unpack('<H' if container == 1 else '<I', data)[0]
    if l == 0:
        return False, [PyComment('End of pseudo sprites')]
    grf_type = f.read(1)[0]
    grf_type_str = hex(grf_type)[2:]
    data = f.read(l)
    res = [PyComment(f'{nfo_line}: Sprite({l}, {grf_type_str}): {hex_str(data, 100)}')]
    if grf_type == 0xff:
        res[0] = PyComment(f'{nfo_line}: Sprite({l}, {grf_type_str}) <{data[0]:02x}>: {hex_str(data, 100)}')
        # print('Sprite', l, hex_str(data))
        decoder = ACTIONS.get(data[0])
        if decoder:
            res.extend(decoder(data[1:]))
        else:
            res.append(PyComment(f'Unsupported action 0x{data[0]:02x}'))
    # else:
    #     if container == 1:
    #         read_real_sprite(f)
    #         return True, res
    return True, res


def decode_sprite(f, num):
    data = b''
    while num > 0:
        code = f.read(1)[0]
        if code >= 128: code -= 256
        # print(f'Code {code} num {num}')
        if code >= 0:
            size = 0x80 if code == 0 else code
            num -= size
            if num < 0: raise RuntimeError('Corrupt sprite')
            data += f.read(size)
        else:
            data_offset = ((code & 7) << 8) | f.read(1)[0]
            #if (dest - data_offset < dest_orig.get()) return WarnCorruptSprite(file, file_pos, __LINE__);
            size = -(code >> 3)
            num -= size
            if num < 0: raise RuntimeError('Corrupt sprite')
            data += data[-data_offset:size - data_offset]
    if num != 0: raise RuntimeError('Corrupt sprite')
    return data


def read_real_sprite(f):
    sprite_id = struct.unpack('<I', f.read(4))[0]
    if sprite_id == 0:
        print(f'End of real sprites')
        return False
    print(f'Real sprite({sprite_id}): ', end='')
    num, t = struct.unpack('<IB', f.read(5))
    start_pos = f.tell()
    print(f'({num}, {t:02x}): ', end='')
    if t == 0xff:
        print('non-real (skip)')
        f.seek(start_pos + num - 1, 0)
        return True
    zoom, height, width, x_offs, y_offs = struct.unpack('<BHHhh', f.read(9))
    bpp = 1  # TODO
    decomp_size = struct.unpack('<I', f.read(4))[0] if t & 0x08 else width * height * bpp
    print(f'{width}x{height} zoom={zoom} x_offs={x_offs} y_offs={y_offs} bpp={bpp} decomp_size={decomp_size}')
    # data = decode_sprite(f, decomp_size)
    # print('Data: ', hex_str(data[:40]))
    f.seek(start_pos + num - 1, 0)
    return True


filename = sys.argv[1]
with open(filename, 'rb') as f:
    first = f.read(1)
    container = None
    gen = grf.BaseNewGRF()
    comment = lambda text: gen.add(PyComment(text))
    if first == b'\00':
        header_bytes = first + f.read(9)
        comment(f'New container header: {hex_str(header_bytes)}')
        data_offest, compression = struct.unpack('<IB', f.read(5))
        header_offset = f.tell() - 1
        comment(f'Offset: {data_offest} compresion: {compression}')
        magic_sprite_bytes = f.read(5 + 4)
        comment(f'Magic sprite: {hex_str(magic_sprite_bytes)}')
        container = 2
    else:
        f.seek(0, 0)
        comment(f'Old container, no header!')
        container = 1
        magic_sprite_bytes = f.read(5 + 2)
        comment(f'Magic sprite: {hex_str(magic_sprite_bytes)}')

    # print('Magic sprite:', hex_str(f.read(4)))
    nfo_line = 1
    while (res := read_pseudo_sprite(f, nfo_line, container))[0]:
        gen.add(*res[1])
        nfo_line += 1

    if container == 2:
        real_data_offset = f.tell() - header_offset
        # while read_real_sprite(f):
        #     pass
            # nfo_line += 1

        if data_offest != real_data_offset:
            comment(f'[ERROR] Data offset check failed: {data_offest} {real_data_offset}')

    print(f'# This file is generated by decompiling {filename} with grftopy.')
    print('# It\'s is only intended to be used as reference, don\'t expect it to actually run and produce grf.')
    print('')
    print(gen.generate_python())
