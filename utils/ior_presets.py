"""Index of refraction and dispersion preset values, used by the IOR Preset
    node (nodes/textures/iorpreset) and its operators (operators/ior_presets)"""
# <pep8 compliant>
import copy

# Refractive Index value references:
#   https://refractiveindex.info/
#   https://en.wikipedia.org/wiki/List_of_refractive_indices
#   https://en.wikipedia.org/wiki/Calcite
#
# Abbe numbers V_d = (n_d - 1) / (n_F - n_C), computed from the
# refractiveindex.info database pages listed next to each entry (the Schott
# and BOROFLOAT pages give the catalogue value). None means no dispersion
# data, or a dispersion too small to matter (air).

# Fraunhofer F and C lines (486.1327 nm and 656.2725 nm), in micrometers
_LAMBDA_F = 0.4861327
_LAMBDA_C = 0.6562725
# Cauchy's equation n = A + B / lambda^2 gives n_F - n_C = B * _CAUCHY_FC
_CAUCHY_FC = 1 / _LAMBDA_F ** 2 - 1 / _LAMBDA_C ** 2


def abbe_to_cauchy_b(ior, abbe):
    """Cauchy B coefficient (the glass dispersion, in um^2) for an Abbe number"""
    if not abbe or ior <= 1:
        return 0
    return (ior - 1) / (abbe * _CAUCHY_FC)


class LuxCoreIORPresetValues():
    """Class to contain IOR preset items, and return them in
       a list of tuples suitable for an EnumProperty"""

    # ior_values format: 'ior_values["unique_id"] = ["name", value, abbe]'
    # The ior_values items can be listed here in any order, as they will
    # be sorted when they are converted to a list as required by EnumProperty.
    # Keep new items at the end: the enum index follows this order.
    _ior_values = {
        "acetone": ["Acetone", 1.36, 54.46],  # C3H6O/Rheims
        "acrylic_glass": ["Acrylic Glass", 1.49, 58.00],  # PMMA/Szczurowski
        "air_0C": ["Air @ 0C", 1.000293, None],
        "air_stp": ["Air @ STP", 1.000277, None],
        "amber": ["Amber", 1.55, None],
        "blood": ["Blood", 1.35, 53.70],  # blood/Liu
        "calcite_e": ["Calcite (extraordinary)", 1.486, 79.17],  # CaCO3/Ghosh-e
        "calcite_o": ["Calcite (ordinary)", 1.658, 49.91],  # CaCO3/Ghosh-o
        "cellulose": ["Cellulose", 1.47, 54.00],  # cellulose/Sultanova
        "crown_glass_pure": ["Crown glass (pure)", 1.52, 64.17],  # Schott N-BK7
        "cubic_zirconia": ["Cubic zirconia", 2.15, 33.54],  # ZrO2-Y2O3/Wood
        "diamond": ["Diamond", 2.415, 55.30],  # C/Peter
        "ethanol_20C": ["Ethanol @ 20C", 1.36, 59.35],  # C2H6O/Rheims
        "flint_glass_dense": ["Flint glass (dense)", 1.71, 29.51],  # Schott SF1
        "flint_glass_pure": ["Flint glass (pure)", 1.61, 36.37],  # Schott F2
        "glycerine": ["Glycerine", 1.47, 64.39],  # C3H8O3/Gupta
        "nacl": ["Sodium Chloride", 1.544, 42.89],  # NaCl/Li
        "olive_oil": ["Olive Oil", 1.46, None],
        "opal": ["Opal", 1.44, None],
        "pet_plastic": ["PET plastic", 1.5750, 27.87],  # PET/Zhang
        "plate_glass": ["Plate Glass", 1.52, 63.97],  # soda-lime/Rubin-clear
        "polycarbonate": ["Polycarbonate", 1.584, 27.86],  # PC/Sultanova
        "polystyrene": ["Polystyrene", 1.59, 29.53],  # PS/Sultanova
        "pyrex_glass": ["Pyrex Glass", 1.470, 65.41],  # Schott BOROFLOAT 33
        "quartz": ["Quartz", 1.458, 67.82],  # fused silica, SiO2/Malitson
        "sapphire": ["Sapphire", 1.77, 72.31],  # Al2O3/Malitson-o
        "silicone_oil_20C": ["Silicone Oil @ 20C", 1.4, None],
        "turpentine": ["Turpentine", 1.47, None],
        "vacuum": ["Vacuum", 1, None],
        "water_20C": ["Water @ 20C", 1.330, 55.76],  # H2O/Daimon-20.0C
        "water_ice": ["Water Ice", 1.31, 53.24],  # H2O/Warren-2008
    }

    # A default value that will be assigned an integer index of 0 as required
    # by EnumProperty when using a callback to obtain the list of items.
    default_key = "water_20C"

    @classmethod
    def get_sorted_list(cls, sort="name"):
        # Make a deep copy of the ior_values dict
        ior_values = copy.deepcopy(cls._ior_values)
        # Append a unique integer index to each tuple. The EnumProperty needs
        # this to determine a default item (index 0) when using a callback.
        # Failure to do so results in a random item becoming the default.
        ior_values[cls.default_key].append(0)
        index = 1
        for ior in ior_values:
            if ior != cls.default_key:
                ior_values[ior].append(index)
                index += 1
        # Convert the dict to a sorted list of tuples
        if sort == "value":
            index = 1
        else:
            index = 0
        preset_list = []
        for item in sorted(ior_values.items(),
                           key=lambda e: e[1][index]):
            text = "{} ({:f})".format(item[1][0], item[1][1])
            preset_list.append((item[0], text, text, item[1][3]))
        return preset_list

    @classmethod
    def get_item(cls, key=default_key):
        return cls._ior_values[key]

    @classmethod
    def get_value(cls, key=default_key):
        return cls._ior_values[key][1]

    @classmethod
    def find_by_name(cls, name):
        """The item with this display name, or None"""
        for item in cls._ior_values.values():
            if item[0] == name:
                return item
        return None
