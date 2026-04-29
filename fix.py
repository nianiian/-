with open('pipeline_controller.py', 'r', encoding='utf-8') as f:
    text = f.read()

search_str = '''        # -------------------------------------------------------------
        # Step 04: Extraction
        # -------------------------------------------------------------
        print(f\"\\n{'*'*80}\")
        print(\"STARTING FULL PIPELINE FOR ALL COMPOUNDS\")'''

replace_str = '''        # -------------------------------------------------------------
        # Step 04: Extraction
        # -------------------------------------------------------------
        step_results[\"step04\"] = self.run_step04(compound, compound_dir)
        compound_progress.set_description(f\"{compound} - All steps completed\")
        
        compound_progress.update(1)
        self.logger.info(f\"Pipeline completed for {compound}\")
        return step_results
    
    def run_full_pipeline(self) -> dict[str, dict[str, bool]]:
        \"\"\"Run pipeline for all compounds.\"\"\"
        print(f\"\\n{'*'*80}\")
        print(\"STARTING FULL PIPELINE FOR ALL COMPOUNDS\")'''

text = text.replace(search_str, replace_str)

with open('pipeline_controller.py', 'w', encoding='utf-8') as f:
    f.write(text)
