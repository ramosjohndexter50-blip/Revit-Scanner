# -*- coding: utf-8 -*-
DEFAULT_CONFIG = {
    "hard_clash_categories": [
        ["OST_Walls", "OST_Walls"],
        ["OST_Walls", "OST_Floors"],
        ["OST_Walls", "OST_StructuralFraming"],
        ["OST_Walls", "OST_StructuralColumns"],
        ["OST_Floors", "OST_StructuralFraming"],
        ["OST_Floors", "OST_StructuralColumns"],
        ["OST_Doors", "OST_Walls"],
        ["OST_Windows", "OST_Walls"],
        ["OST_DuctCurves", "OST_PipeCurves"],
        ["OST_DuctCurves", "OST_CableTray"],
        ["OST_PipeCurves", "OST_CableTray"]
    ],
    "clearance_tolerances_mm": {
        "OST_DuctCurves|OST_Walls": 25,
        "OST_PipeCurves|OST_Walls": 25,
        "OST_DuctCurves|OST_StructuralFraming": 50,
        "OST_PipeCurves|OST_StructuralFraming": 50,
        "OST_CableTray|OST_Walls": 25
    },
    "include_linked_models": True,
    "geometry_cache_elements": 128,
    "minimum_clash_volume_mm3": 1.0,
    "ignore_hosted_elements": True,
    "ignore_nested_or_shared_family_components": True,
    "ignore_same_group": True,
    "ignore_same_assembly": True,
    "duplicate_location_tolerance_mm": 10,
    "family_size_threshold_mb": 20,
    "required_parameters": {
        "OST_Doors": ["Mark", "Type Mark", "Level"],
        "OST_Windows": ["Mark", "Type Mark", "Level"],
        "OST_Walls": ["Type", "Base Constraint", "Top Constraint"],
        "OST_Floors": ["Type", "Level"],
        "OST_StructuralColumns": ["Mark", "Base Level", "Top Level"],
        "OST_StructuralFraming": ["Mark", "Reference Level"]
    },
    "naming_regex": {
        "views": r"^(A|S|M|E|P|BIM)[-_ ].+",
        "sheets": r"^[A-Z]{1,3}[-.]?\d{2,4}.*",
        "levels": r"^(Level|L|Roof|Ground).+",
        "grids": r"^[A-Z0-9]+$",
        "worksets": r"^(AR|ST|MEP|EL|PL|Shared|Links)[-_ ].+"
    },
    "score_thresholds": {"pass": 80, "warning": 60},
    "health_weights": {
        "hard_clash": 8,
        "clearance_clash": 3,
        "critical_revit_warning": 5,
        "high_revit_warning": 3,
        "medium_revit_warning": 1,
        "missing_parameter": 1,
        "naming_issue": 1,
        "workset_issue": 2,
        "duplicate": 3,
        "link_coordination": 4,
        "performance_flag": 1
    }
}

DEFAULT_CONFIG.update({
    'tag_required_categories': ['OST_Doors', 'OST_Windows'],
    'annotation_view_name_regex': '.*',
    'sheet_layout_tolerance_mm': 2.0,
    'titleblock_required_parameters': ['SHEET_NUMBER', 'SHEET_NAME']
})
