import json
import re

def update_controller():
    with open('pipeline_controller.py', 'r', encoding='utf-8') as f:
        text = f.read()

    # Update check_step04_has_data to check for harm data instead of dosage
    old_check = """        try:
            with open(step04_summary_file, 'r', encoding='utf-8') as f:
                summary = json.load(f)
                
            stats = summary.get("statistics", {})
            useful_data_count = (
                stats.get("extracted", 0) + 
                stats.get("partial_data", 0) + 
                stats.get("partial_with_calculation", 0)
            )
            
            return useful_data_count > 0
            
        except Exception as e:"""
    new_check = """        try:
            with open(step04_summary_file, 'r', encoding='utf-8') as f:
                summary = json.load(f)
                
            stats = summary.get("statistics", {})
            # Now we just check if any safety/harm analysis was successfully extracted
            useful_data_count = stats.get("harm_extracted", 0)
            
            return useful_data_count > 0
            
        except Exception as e:"""

    text = text.replace(old_check, new_check)

    # Change warning texts to reflect the change from dosage to harm
    text = text.replace('found no useful dosage data', 'found no useful safety/harm data')
    text = text.replace('usable dosage', 'valid safety context')

    with open('pipeline_controller.py', 'w', encoding='utf-8') as f:
        f.write(text)
    print("Updated pipeline controller.")

if __name__ == '__main__':
    update_controller()
