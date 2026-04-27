import streamlit as st
import requests
import pyalex
from pyalex import Works
import re 
from datetime import datetime, timedelta
import pandas as pd

@st.cache_data(ttl=timedelta(days=1), show_spinner="Fetching trials...")
def fetch_trials(cond, intr, start_dt, today, expansion_type):

    #fetch completed trials in the past X years 
    url = "https://clinicaltrials.gov/api/v2/studies"
    params = {
            "query.cond": f'EXPANSION[{expansion_type}]{cond}',
            "filter.advanced": f"AREA[LastUpdatePostDate]RANGE[{start_dt.strftime('%Y-%m-%d')}, {today.strftime('%Y-%m-%d')}]",
            "pageSize": 1000, # Note: If your query has >1000 results, we'll need pagination later!
        }
        
    #Add the intervention ONLY if 'intr' is not empty
    if intr:
        params["query.intr"] = f'EXPANSION[{expansion_type}]{intr}'

    try:
        resp = requests.get(url, params=params)
        return resp.json().get('studies', []) if resp.status_code == 200 else resp.raise_for_status()
    except Exception as e:
        st.error(f"Error fetching trials: {resp.request.url}")
        raise e

def is_completed_after(study, cutoff_date):
    """Safely checks if a study was completed after the cutoff date."""
    status_module = study.get('protocolSection', {}).get('statusModule', {})
    
    if status_module.get('overallStatus') != 'COMPLETED':
        return False
    
    pcd_str = status_module.get('primaryCompletionDateStruct', {}).get('date')
    
    if pcd_str:
        try:
            fmt = '%Y-%m-%d' if len(pcd_str) > 7 else '%Y-%m'
            pcd_dt = datetime.strptime(pcd_str, fmt)
            return pcd_dt > cutoff_date
        except ValueError:
            return False
    return False

def create_wide_export_dataframe(trials):
    """Flattens all PICO and overview data into a single wide dataframe for CSV export."""
    rows = []
    for s in trials:
        protocol = s.get('protocolSection', {})
        nct_id = protocol.get('identificationModule', {}).get('nctId', '')
        
        # Overview
        design = protocol.get('designModule', {})
        status_mod = protocol.get('statusModule', {})
        sponsor_mod = protocol.get('sponsorCollaboratorsModule', {})
        
        # Population
        eligibility = protocol.get('eligibilityModule', {})
        
        # Interventions
        arms_interventions = protocol.get('armsInterventionsModule', {})
        int_list = arms_interventions.get('interventions', [])
        arms_list = arms_interventions.get('armGroups', [])
        
        # Outcomes
        outcomes = protocol.get('outcomesModule', {})
        prim_out = outcomes.get('primaryOutcomes', [])
        sec_out = outcomes.get('secondaryOutcomes', [])

        rows.append({
            "NCT ID": nct_id,
            "Trial Name": protocol.get('identificationModule', {}).get('briefTitle', 'No Title'),
            "URL": f"https://clinicaltrials.gov/study/{nct_id}",
            "Status": status_mod.get('overallStatus', ''),
            "Start Date": status_mod.get('startDateStruct', {}).get('date', ''),
            "Completion Date": status_mod.get('completionDateStruct', {}).get('date', ''),
            "Study Type": design.get('studyType', ''),
            "Phases": " | ".join(design.get('phases', [])),
            "Enrollment": design.get('enrollmentInfo', {}).get('count', 'N/A'),
            "Sponsor": sponsor_mod.get('leadSponsor', {}).get('name', ''),
            "Sex": eligibility.get('sex', 'Not Specified'),
            "Min Age": eligibility.get('minimumAge', '0 Years'),
            "Max Age": eligibility.get('maximumAge', 'No Limit'),
            "Eligibility Criteria": eligibility.get('eligibilityCriteria', 'Not Provided'),
            "Arms / Groups": " | ".join([f"{a.get('type', 'Arm')}: {a.get('label', '')}" for a in arms_list]),
            "Interventions Details": " | ".join([f"{i.get('type', '')}: {i.get('name', '')}" for i in int_list]),
            "Primary Outcomes": " | ".join([f"{o.get('measure', '')} ({o.get('timeFrame', '')})" for o in prim_out]),
            "Secondary Outcomes": " | ".join([f"{o.get('measure', '')} ({o.get('timeFrame', '')})" for o in sec_out])
        })
    return pd.DataFrame(rows)

def extract_trial_dataframes(trials):
    """Extracts trial data into four dataframes for UI display (Overview, Population, Interventions, Outcomes)."""
    df_overview_list = []
    df_population_list = []
    df_intervention_list = []
    df_outcome_list = []

    for s in trials:
        protocol = s.get('protocolSection', {})
        nct_id = protocol.get('identificationModule', {}).get('nctId', '')
        title = protocol.get('identificationModule', {}).get('briefTitle', 'No Title')
        url = f"https://clinicaltrials.gov/study/{nct_id}"

        # 1. OVERVIEW DATA
        design = protocol.get('designModule', {})
        status_mod = protocol.get('statusModule', {})
        sponsor_mod = protocol.get('sponsorCollaboratorsModule', {})
        
        df_overview_list.append({
            "NCT ID": nct_id,
            "Trial Name": title,
            "Status": status_mod.get('overallStatus', 'N/A'),
            "Phases": " | ".join(design.get('phases', [])),
            "Study Type": design.get('studyType', ''),
            "Enrollment": design.get('enrollmentInfo', {}).get('count', 'N/A'),
            "Sponsor": sponsor_mod.get('leadSponsor', {}).get('name', ''),
            "Start Date": status_mod.get('startDateStruct', {}).get('date', 'N/A'),
            "Completion Date": status_mod.get('completionDateStruct', {}).get('date', 'N/A'),
            "URL": url
        })

        # 2. POPULATION / ELIGIBILITY DATA
        eligibility = protocol.get('eligibilityModule', {})
        df_population_list.append({
            "NCT ID": nct_id,
            "Sex": eligibility.get('sex', 'Not Specified'),
            "Min Age": eligibility.get('minimumAge', '0 Years'),
            "Max Age": eligibility.get('maximumAge', 'No Limit'),
            "Eligibility Criteria": eligibility.get('eligibilityCriteria', 'Not Provided'),
            "URL": url
        })

        # 3. INTERVENTION DATA
        arms_interventions = protocol.get('armsInterventionsModule', {})
        interventions_list = arms_interventions.get('interventions', [])
        arms_list = arms_interventions.get('armGroups', [])
        
        int_str = "\n\n".join([f"• {i.get('type', '')}: {i.get('name', '')}\n  {i.get('description', '')}" for i in interventions_list])
        arms_str = "\n\n".join([f"• {a.get('type', 'Arm')}: {a.get('label', '')}\n  {a.get('description', '')}" for a in arms_list])

        df_intervention_list.append({
            "NCT ID": nct_id,
            "Arms / Groups": arms_str,
            "Interventions Details": int_str,
            "URL": url
        })

        # 4. OUTCOME DATA
        outcomes_module = protocol.get('outcomesModule', {})
        primary_outcomes = outcomes_module.get('primaryOutcomes', [])
        secondary_outcomes = outcomes_module.get('secondaryOutcomes', [])
        
        out_str = "\n\n".join([f"• {o.get('measure', '')}\n  Time Frame: {o.get('timeFrame', '')}" for o in primary_outcomes])
        sec_out_str = "\n\n".join([f"• {o.get('measure', 'N/A')}\n  Time Frame: {o.get('timeFrame', 'N/A')}" for o in secondary_outcomes])
        df_outcome_list.append({
            "NCT ID": nct_id,
            "Primary Outcomes": out_str,
            "Secondary Outcomes" : sec_out_str,
            "URL": url
        })

    return pd.DataFrame(df_overview_list), pd.DataFrame(df_population_list), pd.DataFrame(df_intervention_list), pd.DataFrame(df_outcome_list)