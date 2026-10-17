"""Flow-regime mapping used by the MDBA's EWR_analysis_pipeline (confirmed source of truth for this dashboard).

Copied from `0.shared mappings and paths.ipynb` (cell defining `flow_mappings`) and the matching rule from
`3.post_processing/10.frequency analysis.ipynb` (`map_and_explode_flow_regime`), with overlapping keys resolved
by specificity:
  * the EWR's State (parameter sheet) picks the sub-dictionary;
  * the longest key that is a substring of the EWR Code wins (`key in code`);
  * this specificity rule deliberately diverges from the upstream first-match rule to resolve overlapping keys correctly;
  * a list value means the EWR belongs to every listed regime (the pipeline explodes it into one row per regime);
  * no key matching -> no regime (the pipeline leaves FlowRegime empty).
https://github.com/MDBAuth/EWR_analysis_pipeline
"""

flow_mappings = {
    'QLD': {
        'BBR1_a': ['Overbank', 'Wetland inundation'],
        'BBR1_b': ['Overbank', 'Wetland inundation'],
        'BBR2': ['Overbank', 'Wetland inundation'],
        'BF-FD1': 'Baseflow',
        'BF-FD2': 'Baseflow',
        'dANA': 'Fresh',
        'DR_S': 'Bankfull',
        'DR_F': 'Baseflow',
        'FD2': ['Cease to flow', 'Fresh'],
        'FD1': ['Baseflow', 'Cease to flow'],
        'FR1': 'Fresh',
        'FrC': 'Fresh',
        'FrW1': 'Baseflow',
        'FrW2': 'Baseflow',
        'FrW3': 'Baseflow',
        'FrW4': 'Baseflow',
        'LDF': 'Fresh',
        'rANA': 'Fresh',
        'SF-FD1': 'Baseflow',
        'SF-FD2': 'Fresh',
        'FrL1': 'excluded',
        'FrL2': 'excluded',
        'WL': 'Wetland inundation',
    },
    'NSW': {
        'OB-L1': 'Overbank large',
        'OB-S1': 'Overbank small',
        'OB-S2': 'Overbank small',
        'W-LF4': 'Wetland Large Fresh',
        'W-LF5': 'Wetland Large Fresh',
        'W-LF6': 'Wetland Large Fresh',
        'W-LF7': 'Wetland Large Fresh',
        'W-LF8': 'Wetland Large Fresh',
        'NestS1': 'Nesting support flow',
        'HLLF': 'High lake level fill',
        'LLLF': 'Low level lake fill',
        'MLLF': 'Mid level lake fill',
        'VHLL': 'Very high level fill',
        'VF': 'Very low flow', 
        'BF': 'Baseflow', 
        'SF': 'Small fresh', 
        'LF': 'Large fresh', 
        'BK': 'Bankfull', 
        'OB': 'Overbank', 
        'WL': 'Wetland inundation',
        'CF': 'Cease to flow',
        'AC': 'Annabranch connection'
    },
    'VIC': {
        'VF': 'Very low flow', 
        'F': 'Fresh', 
        'BF': 'Baseflow', 
        'SF': 'Small fresh', 
        'LF': 'Large fresh', 
        'BK': 'Bankfull', 
        'OB': 'Overbank', 
        'Wetland': 'Wetland inundation',
        'CF': 'Cease to flow',
        'AC': 'Annabranch connection',
    },
    'ACT': {
        'BF': 'Baseflow',
        'SF': 'Small fresh',
        'LF': 'Large fresh'
    },
    'SA': {
        'IC1' : 'Baseflow',
        'IC2' : 'Small fresh',
        'IC3' : 'Large fresh',
        'IC4' : 'Bankfull flow',
        'FP1' : 'Low overbank flow',
        'FP2' : 'Low to moderate overbank flow',
        'FP3': 'Moderate overbank flow',
        'FP4':  'Moderate to large overbank flow',
        'FP5': 'Moderate to large overbank flow',
        'CLLMM1c': 'lower lakes',
        'CLLMM2c': 'lower lakes',
        'CLLMM3c': 'lower lakes',
        'CLLMM4c': 'lower lakes',
        'CLLMM1d': 'coorong',
        'CLLMM2d': 'coorong',
        'CLLMM3d': 'coorong',
        'CLLMM1a': 'Annual Barrage flow',
        'CLLMM1b': 'three yearly barrage flows',
        'CLLMM2a': 'Annual Barrage flow',
        'CLLMM2b': 'three yearly barrage flows',
        'CLLMM3a': 'Annual Barrage flow',
        'CLLMM4a': 'Annual Barrage flow'
    }
}


def regimes_for(code, state):
    """Pipeline flow regimes (list of names) for an EWR code in a state; [] when the pipeline gives none."""
    matches = ((key, val) for key, val in flow_mappings.get(state, {}).items() if key in code)
    match = max(matches, key=lambda item: len(item[0]), default=None)
    if match is None:
        return []
    val = match[1]
    return list(val) if isinstance(val, list) else [val]
