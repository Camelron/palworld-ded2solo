"""Property types that palworld-save-tools 0.24.0 cannot read.

Applied as delegating monkeypatches rather than a forked ``archive.py`` so that
none of upstream's code is copied here -- each wrapper handles only the new type
and hands everything else back to the original implementation.

Two gaps show up on post-2026-update saves:

``Int64Property`` (and friends) as a *map value*
    ``.worldSaveData.LevelObjectRecoverPartySaveData.Value.PlayerLastUsedTimes``
    is a map of Guid -> Int64, but ``prop_value`` only knew Struct/Enum/Name/
    Int/Bool. Float, Double and Str are added at the same time; they cost
    nothing and the same gap would hit them.

``SetProperty``
    ``.worldSaveData.InLockerCharacterInstanceIDArray`` is a set, which upstream
    does not implement at all. On-disk layout, after the usual name/type/size
    property header::

        fstring   element type        ("StructProperty")
        byte      optional-guid flag
        u32       removed-entry count (0 in practice)
        u32       element count
        ...       elements, back to back

    Note the element struct type must default to ``StructProperty``, *not*
    ``Guid``: the elements are nested property lists (PlayerUId / InstanceId /
    DebugName), and defaulting to ``Guid`` makes the reader consume 16 raw bytes
    and desynchronise the whole stream.
"""

from palworld_save_tools.archive import FArchiveReader, FArchiveWriter

# Map/set value types upstream's prop_value does not handle, paired with the
# reader method and writer method for each.
_SCALARS = {
    "Int64Property": ("i64", "i64"),
    "FloatProperty": ("float", "float"),
    "DoubleProperty": ("double", "double"),
    "StrProperty": ("fstring", "fstring"),
}

_applied = False


def apply() -> None:
    """Install the extensions. Safe to call more than once."""
    global _applied
    if _applied:
        return
    _applied = True

    reader_prop_value = FArchiveReader.prop_value
    reader_property = FArchiveReader.property
    writer_prop_value = FArchiveWriter.prop_value
    writer_property_inner = FArchiveWriter.property_inner

    def read_prop_value(self, type_name, struct_type_name, path):
        if type_name in _SCALARS:
            return getattr(self, _SCALARS[type_name][0])()
        return reader_prop_value(self, type_name, struct_type_name, path)

    def read_property(self, type_name, size, path, nested_caller_path=""):
        if type_name != "SetProperty" or path in self.custom_properties:
            return reader_property(self, type_name, size, path, nested_caller_path)
        set_type = self.fstring()
        _id = self.optional_guid()
        removed_count = self.u32()
        count = self.u32()
        element_path = path + ".Set"
        struct_type = (
            self.get_type_or(element_path, "StructProperty")
            if set_type == "StructProperty"
            else None
        )
        value = {
            "set_type": set_type,
            "set_struct_type": struct_type,
            "id": _id,
            "removed_count": removed_count,
            "value": [
                self.prop_value(set_type, struct_type, element_path)
                for _ in range(count)
            ],
            "type": type_name,
        }
        return value

    def write_prop_value(self, type_name, struct_type_name, value):
        if type_name in _SCALARS:
            return getattr(self, _SCALARS[type_name][1])(value)
        return writer_prop_value(self, type_name, struct_type_name, value)

    def write_property_inner(self, property_type, property):
        if property_type != "SetProperty" or "custom_type" in property:
            return writer_property_inner(self, property_type, property)
        self.fstring(property["set_type"])
        self.optional_guid(property.get("id", None))
        nested = self.copy()
        nested.u32(property.get("removed_count", 0))
        nested.u32(len(property["value"]))
        for element in property["value"]:
            nested.prop_value(
                property["set_type"], property["set_struct_type"], element
            )
        buf = nested.bytes()
        self.write(buf)
        return len(buf)

    FArchiveReader.prop_value = read_prop_value
    FArchiveReader.property = read_property
    FArchiveWriter.prop_value = write_prop_value
    FArchiveWriter.property_inner = write_property_inner
