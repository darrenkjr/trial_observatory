import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timedelta
import pyalex
from pyalex import Works
import os
import re

pyalex.config.api_key = st.secrets["oa_apikey"]

# --- App Config ---
st.set_page_config(page_title="TrialTrackr", page_icon="🔬", layout="wide")
st.title("🔬 MCHRI Living Clinical Trial Observatory")
st.info("This is an experimental tool to help you track the status of clinical trials for a given PICO question for evidence surveillance purposes (eg: updating a systematic review or guideline). Main source of data is clinicaltrials.gov, with publication data from OpenAlex where possible.") 
st.write("It is a work in progress and will be updated regularly. Future work will involve linking NCTIDs to published articles and more. For feature requests, please contact darren.rajit@monash.edu")

# --- Sidebar Inputs ---
st.sidebar.header("Search Parameters")
condition = st.sidebar.text_input("Condition", value="PCOS")
intervention = st.sidebar.text_input("Intervention (e.g., Metformin)", value="")
expansion_type = st.sidebar.selectbox("Term Expansion Type", ["Relaxation", "None", "Term", "Concept", "Lossy"], index=0)
lookback_years = st.sidebar.slider("Lookback Period (Years)", 1, 10, 3)
# Calculate dates
today = datetime.now()
start_date = today - timedelta(days=lookback_years * 365)
one_year_ago = today - timedelta(days=365)

# --- API Fetching Logic ---
@st.cache_data(ttl=3600) # Cache results for 1 hour to stay fast
def fetch_trials(cond, intr, start_dt):

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

if st.sidebar.button("Run Search"):
    with st.spinner(f"Searching for {condition} with {intervention} trials..."):
        all_studies = fetch_trials(condition, intervention, start_date)
    
    if not all_studies:
        st.warning("No trials found for those parameters.")
        raise Exception("No trials found for those parameters.")
    else:
        # --- Data Processing ---
        # 1. Completed
        completed = [s for s in all_studies if is_completed_after(s, start_date)]
        comp_res = [s for s in completed if s.get('hasResults')]
        comp_res_count = len(comp_res)
        comp_no_res = [s for s in completed if not s.get('hasResults')] # <--- ADD THIS LINE
        comp_res_count = len(comp_res)
        
        # 2. Overdue Logic
        overdue_ids = []
        unknown_ids = []
        for s in completed:
            pcd = s['protocolSection']['statusModule'].get('primaryCompletionDateStruct', {}).get('date')
            if pcd and not s.get('hasResults'):
                pcd_dt = datetime.strptime(pcd, '%Y-%m-%d') if len(pcd) > 7 else datetime.strptime(pcd, '%Y-%m')
                if pcd_dt < one_year_ago:
                    overdue_ids.append(s['protocolSection']['identificationModule']['nctId'])
            else: 
                unknown_ids.append(s['protocolSection']['identificationModule']['nctId'])
        
        # 3. Terminated & Active
        term_statuses = ['SUSPENDED', 'TERMINATED', 'WITHDRAWN']
        terminated = [s for s in all_studies if s['protocolSection']['statusModule']['overallStatus'] in term_statuses]
        active_statuses = ['RECRUITING', 'ACTIVE_NOT_RECRUITING', 'ENROLLING_BY_INVITATION', 'NOT_YET_RECRUITING']
        active = [s for s in all_studies if s['protocolSection']['statusModule']['overallStatus'] in active_statuses]
        active_res = [s for s in active if s.get('hasResults')]
        active_res_count = len(active_res)

        # --- Dashboard Layout ---
        st.subheader(f"Results for '{condition}' and '{intervention}' since {start_date.year}" if intervention else f"Results for '{condition}' since {start_date.year}")
        
        # Top Row Metrics
        m1, m2, m3 = st.columns(3)
        m1.metric("Completed", len(completed))
        m2.metric("Active Trials", len(active))
        m3.metric("Terminated", len(terminated))

        st.divider()

        # Detailed Breakdown
        col_left, col_right = st.columns(2)
        
        with col_left:
            st.markdown("### ✅ Completed Trials")
            completed_enrolled = sum([s['protocolSection']['designModule']['enrollmentInfo']['count'] for s in completed if s.get('protocolSection', {}).get('designModule', {}).get('enrollmentInfo', {}).get('count')])
            m1_2, m2_2, m3_2, m4_2 = st.columns(4)
            m1_2.metric("Completed", len(completed))
            m2_2.metric("Enrolled Participants", completed_enrolled)
            m3_2.metric("Results Available", comp_res_count)
            m4_2.metric("Overdue* Trials", len(overdue_ids))

            st.info("*Overdue trials are trials that have completed more than 1 year ago but have not submitted results to clinicaltrials.gov")

            if overdue_ids:
                st.error(f"🚨 {len(overdue_ids)} trials are overdue to submit results (>1 year since completion)")
                with st.expander("View Overdue NCT IDs"):
                    st.write(", ".join(overdue_ids))

        with col_right:
            st.markdown("### 🏃 Active Trials")
            m1_3, m2_3, m3_3, m4_3 = st.columns(4)
            m1_3.metric("Recruiting", len([s for s in active if s['protocolSection']['statusModule']['overallStatus'] == 'RECRUITING']))
            m2_3.metric("Not Recruiting*", len([s for s in active if s['protocolSection']['statusModule']['overallStatus'] == 'ACTIVE_NOT_RECRUITING']))
            m3_3.metric("Enrolling by Invitation", len([s for s in active if s['protocolSection']['statusModule']['overallStatus'] == 'ENROLLING_BY_INVITATION']))
            m4_3.metric("Not Yet Recruiting", len([s for s in active if s['protocolSection']['statusModule']['overallStatus'] == 'NOT_YET_RECRUITING']))

            st.info('*Participants are receiving an intervention or being examined, but no new participants are being recruited or enrolled')

        st.divider()
        
        # --- NEW TABBED SECTION FOR COMPLETED TRIALS ---
        st.markdown("## 📊 Completed Trials PICO View (With Posted Results)")
        
        df_overview_list = []
        df_population_list = []
        df_intervention_list = []
        df_outcome_list = []

        for s in comp_res:
            nct_id = s['protocolSection']['identificationModule']['nctId']
            title = s['protocolSection']['identificationModule'].get('briefTitle', 'No Title')
            url = f"https://clinicaltrials.gov/study/{nct_id}"
            protocol = s.get('protocolSection', {})

            # 1. OVERVIEW DATA
            design = protocol.get('designModule', {})
            status_mod = protocol.get('statusModule', {})
            sponsor_mod = protocol.get('sponsorCollaboratorsModule', {})
            
            df_overview_list.append({
                "NCT ID": nct_id,
                "Trial Name": title,
                "Phases": " | ".join(design.get('phases', [])),
                "Study Type": design.get('studyType', ''),
                "Enrollment": design.get('enrollmentInfo', {}).get('count', 'N/A'),
                "Sponsor": sponsor_mod.get('leadSponsor', {}).get('name', ''),
                "Start Date": status_mod.get('startDateStruct', {}).get('date', ''),
                "Completion Date": status_mod.get('completionDateStruct', {}).get('date', ''),
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
            secondary_outcomes = outcomes_module.get('secondaryOutcomes',[])
            
            out_str = "\n\n".join([f"• {o.get('measure', '')}\n  Time Frame: {o.get('timeFrame', '')}" for o in primary_outcomes])
            sec_out_str = "\n\n".join([f"• {o.get('measure', 'N/A')}\n  Time Frame: {o.get('timeFrame', 'N/A')}" for o in secondary_outcomes])
            df_outcome_list.append({
                "NCT ID": nct_id,
                "Primary Outcomes": out_str,
                "Secondary Outcomes" : sec_out_str,
                "URL": url
            })

        # Render the Tabs
        if comp_res:
            tab1, tab2, tab3, tab4 = st.tabs(["📋 Trial Overview", "👥 Population & Eligibility", "💊 Interventions", "🎯 Outcomes"])

            with tab1:
                st.dataframe(pd.DataFrame(df_overview_list), column_config={"URL": st.column_config.LinkColumn("Link", display_text="View on CT.gov")}, hide_index=True, use_container_width=True)

            with tab2:
                st.dataframe(pd.DataFrame(df_population_list), column_config={"URL": st.column_config.LinkColumn("Link", display_text="View on CT.gov"), "Eligibility Criteria": st.column_config.TextColumn("Eligibility Criteria", width="large")}, hide_index=True, use_container_width=True)

            with tab3:
                st.dataframe(pd.DataFrame(df_intervention_list), column_config={"URL": st.column_config.LinkColumn("Link", display_text="View on CT.gov"), "Arms / Groups": st.column_config.TextColumn("Arms / Groups", width="large"), "Interventions Details": st.column_config.TextColumn("Interventions Details", width="large")}, hide_index=True, use_container_width=True)

            with tab4:
                st.dataframe(pd.DataFrame(df_outcome_list), column_config={"URL": st.column_config.LinkColumn("Link", display_text="View on CT.gov"), "Primary Outcomes": st.column_config.TextColumn("Primary Outcomes", width="large")}, hide_index=True, use_container_width=True)

        st.divider()

        # --- PUBLICATIONS SECTION ---
        st.markdown("#### 📚 Related Publication(s) for Each Completed Trial (With Results)")
        st.info("Needs publications are retrieved from a) citations uploaded by trial investigators b) automatically derived from PubMed where NCT IDs have been cited / mentioned.")

        with st.spinner("Fetching citation details from OpenAlex..."):
            citation_display_list = []
            for s in comp_res:
                nct_id = s['protocolSection']['identificationModule']['nctId']
                references = s.get('protocolSection', {}).get('referencesModule', {}).get('references', [])
                for ref in references:
                    citation_text = ref.get('citation', 'N/A')
                    match = re.search(r'10\.\d{4,}/[^\s]+', citation_text)

                    # FIXED: Initialize doi as None to avoid crashing if no match is found!
                    doi = None
                    if match:
                        doi = match.group(0).rstrip('.')
                        
                    citation_display_list.append({
                        "NCT ID": nct_id, 
                        "Citation Text": citation_text,
                        "DOI" : doi,
                        "PMID": ref.get('pmid'),
                        "Type": ref.get('type', 'N/A')
                        })

            if citation_display_list:
                df_citation = pd.DataFrame(citation_display_list)
                
                # Fetch from OpenAlex
                oa_result_fields = ['ids', 'publication_date']
                pmid_list = df_citation['PMID'].unique().tolist() 
                doi_list = [d for d in df_citation['DOI'].unique().tolist() if d is not None] # ensure no None types are sent to API
                
                if doi_list:
                    oa_results = Works().filter_or(doi=doi_list).select(oa_result_fields).get()
                    pub_date_map = {}
                    
                    for work in oa_results:
                        raw_doi_url = work.get('ids', {}).get('doi', '').strip()
                        if raw_doi_url:
                            naked_doi = raw_doi_url.split('doi.org/')[-1].strip().lower()
                            pub_date_map[naked_doi] = work.get('publication_date')

                    df_citation['Publication Date'] = df_citation['DOI'].map(pub_date_map)
                else:
                    df_citation['Publication Date'] = None

                st.dataframe(
                    df_citation,
                    column_config={
                        "NCT ID": st.column_config.TextColumn("NCT ID"),
                        "Citation Text": st.column_config.TextColumn("Citation Text", width="large"),
                        "PMID": st.column_config.TextColumn("PMID"),
                        "DOI": st.column_config.TextColumn("DOI Link"), 
                        "Publication Date": st.column_config.DateColumn("Published"),
                        "Type": st.column_config.TextColumn("Type"),
                    },
                    hide_index=True,
                    use_container_width=True,
                )
            else:
                st.write("No publications found for these trials.")

# --- COMPLETED TRIALS (PENDING RESULTS) SECTION ---
        if comp_no_res:
            st.markdown("## ⏳ Completed Trials PICO View (Pending Results)")
            st.info("These trials have completed their primary endpoints but have not yet posted results on ClinicalTrials.gov.")
            
            df_overview_no_res = []
            df_population_no_res = []
            df_intervention_no_res = []
            df_outcome_no_res = []

            for s in comp_no_res:
                nct_id = s['protocolSection']['identificationModule']['nctId']
                title = s['protocolSection']['identificationModule'].get('briefTitle', 'No Title')
                url = f"https://clinicaltrials.gov/study/{nct_id}"
                protocol = s.get('protocolSection', {})

                # 1. OVERVIEW DATA
                design = protocol.get('designModule', {})
                status_mod = protocol.get('statusModule', {})
                sponsor_mod = protocol.get('sponsorCollaboratorsModule', {})
                
                df_overview_no_res.append({
                    "NCT ID": nct_id,
                    "Trial Name": title,
                    "Phases": " | ".join(design.get('phases', [])),
                    "Study Type": design.get('studyType', ''),
                    "Enrollment": design.get('enrollmentInfo', {}).get('count', 'N/A'),
                    "Sponsor": sponsor_mod.get('leadSponsor', {}).get('name', ''),
                    "Start Date": status_mod.get('startDateStruct', {}).get('date', ''),
                    "Completion Date": status_mod.get('completionDateStruct', {}).get('date', ''),
                    "URL": url
                })

                # 2. POPULATION / ELIGIBILITY DATA
                eligibility = protocol.get('eligibilityModule', {})
                df_population_no_res.append({
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

                df_intervention_no_res.append({
                    "NCT ID": nct_id,
                    "Arms / Groups": arms_str,
                    "Interventions Details": int_str,
                    "URL": url
                })

                # 4. OUTCOME DATA
                outcomes_module = protocol.get('outcomesModule', {})
                primary_outcomes = outcomes_module.get('primaryOutcomes', [])
                secondary_outcomes = outcomes_module.get('secondaryOutcomes',[])
                
                out_str = "\n\n".join([f"• {o.get('measure', '')}\n  Time Frame: {o.get('timeFrame', '')}" for o in primary_outcomes])
                sec_out_str = "\n\n".join([f"• {o.get('measure', 'N/A')}\n  Time Frame: {o.get('timeFrame', 'N/A')}" for o in secondary_outcomes])
                df_outcome_no_res.append({
                    "NCT ID": nct_id,
                    "Primary Outcomes": out_str,
                    "Secondary Outcomes" : sec_out_str,
                    "URL": url
                })

            # Render the Tabs
            tab1_nr, tab2_nr, tab3_nr, tab4_nr = st.tabs(["📋 Trial Overview", "👥 Population & Eligibility", "💊 Interventions", "🎯 Outcomes"])

            with tab1_nr:
                st.dataframe(pd.DataFrame(df_overview_no_res), column_config={"URL": st.column_config.LinkColumn("Link", display_text="View on CT.gov")}, hide_index=True, use_container_width=True)

            with tab2_nr:
                st.dataframe(pd.DataFrame(df_population_no_res), column_config={"URL": st.column_config.LinkColumn("Link", display_text="View on CT.gov"), "Eligibility Criteria": st.column_config.TextColumn("Eligibility Criteria", width="large")}, hide_index=True, use_container_width=True)

            with tab3_nr:
                st.dataframe(pd.DataFrame(df_intervention_no_res), column_config={"URL": st.column_config.LinkColumn("Link", display_text="View on CT.gov"), "Arms / Groups": st.column_config.TextColumn("Arms / Groups", width="large"), "Interventions Details": st.column_config.TextColumn("Interventions Details", width="large")}, hide_index=True, use_container_width=True)

            with tab4_nr:
                st.dataframe(pd.DataFrame(df_outcome_no_res), column_config={"URL": st.column_config.LinkColumn("Link", display_text="View on CT.gov"), "Primary Outcomes": st.column_config.TextColumn("Primary Outcomes", width="large"), "Secondary Outcomes": st.column_config.TextColumn("Secondary Outcomes", width="large")}, hide_index=True, use_container_width=True)

        st.divider()



    # --- EXPORT DATA SECTION ---
        st.markdown("## 📥 Export Data")
        st.write("Download the complete datasets as CSV files for offline analysis.")
        
        col_dl1, col_dl2 = st.columns(2)
        
        # Button 1: Trials WITH Results
        if comp_res:
            df_export_res = create_wide_export_dataframe(comp_res)
            csv_res = df_export_res.to_csv(index=False).encode('utf-8')
            with col_dl1:
                st.download_button(
                    label="📥 Download Completed Trials (With Results)",
                    data=csv_res,
                    file_name=f"{condition}_trials_with_results.csv",
                    mime="text/csv",
                    use_container_width=True
                )
                
        # Button 2: Trials PENDING Results
        if comp_no_res:
            df_export_no_res = create_wide_export_dataframe(comp_no_res)
            csv_no_res = df_export_no_res.to_csv(index=False).encode('utf-8')
            with col_dl2:
                st.download_button(
                    label="📥 Download Completed Trials (Pending Results)",
                    data=csv_no_res,
                    file_name=f"{condition}_trials_pending_results.csv",
                    mime="text/csv",
                    use_container_width=True
                )

else:
    st.warning("👈 Use the sidebar to set your parameters and click 'Run Search'.")