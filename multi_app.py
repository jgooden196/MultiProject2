import os
import logging
import sys
from flask import Flask, request, jsonify
from asana import Client
from datetime import datetime

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger()

# Asana API setup
asana_token = os.environ.get('ASANA_TOKEN')
client = Client.access_token(asana_token)

# Original project ID - will be used as template reference
TEMPLATE_PROJECT_ID = '1209353707682767'

# Constants
STATUS_TASK_NAME = "Project Status"
ESTIMATED_COST_FIELD = "Budget"  # Your actual custom field name
ACTUAL_COST_FIELD = "Actual Cost"  # Your actual custom field name

# Dictionary to store webhook secret dynamically
WEBHOOK_SECRET = {}

def get_project_workspace(project_id):
    """Get the workspace ID for a given project"""
    try:
        project = client.projects.find_by_id(project_id)
        return project['workspace']['gid']
    except Exception as e:
        logger.error(f"Error getting workspace for project {project_id}: {e}")
        return None

def get_all_projects_in_workspace(workspace_id):
    """Get all projects in a workspace"""
    try:
        projects = client.projects.find_all({'workspace': workspace_id})
        return list(projects)
    except Exception as e:
        logger.error(f"Error getting projects for workspace {workspace_id}: {e}")
        return []

def get_custom_fields(project_id):
    """Get the custom field GIDs for Estimated Cost and Actual Cost fields"""
    try:
        # Get all custom field settings for the project
        custom_field_settings = client.custom_field_settings.find_by_project(project_id)
        
        estimated_cost_gid = None
        actual_cost_gid = None
        
        for setting in custom_field_settings:
            field_name = setting['custom_field']['name']
            if field_name == ESTIMATED_COST_FIELD:
                estimated_cost_gid = setting['custom_field']['gid']
            elif field_name == ACTUAL_COST_FIELD:
                actual_cost_gid = setting['custom_field']['gid']
        
        return estimated_cost_gid, actual_cost_gid
    except Exception as e:
        logger.error(f"Error getting custom fields for project {project_id}: {e}")
        return None, None

def find_status_task(project_id):
    """Find the Project Status task in the project"""
    try:
        tasks = client.tasks.find_by_project(project_id)
        for task in tasks:
            if task['name'] == STATUS_TASK_NAME:
                return task['gid']
        return None
    except Exception as e:
        logger.error(f"Error finding status task for project {project_id}: {e}")
        return None

def create_status_task(project_id):
    """Create the Project Status task"""
    try:
        # Get workspace ID for the project
        workspace_id = get_project_workspace(project_id)
        if not workspace_id:
            logger.error(f"Could not get workspace ID for project {project_id}")
            return None
            
        task = client.tasks.create_in_workspace({
            'name': STATUS_TASK_NAME,
            'projects': [project_id],
            'workspace': workspace_id,
            'notes': "This task contains summary information about the project budget."
        })
        logger.info(f"Created Project Status task with GID: {task['gid']} for project {project_id}")
        return task['gid']
    except Exception as e:
        logger.error(f"Error creating status task for project {project_id}: {e}")
        return None

def update_project_metrics(project_id):
    """Calculate project metrics and update the Project Status task"""
    try:
        # Get current timestamp
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        estimated_cost_gid, actual_cost_gid = get_custom_fields(project_id)
        if not estimated_cost_gid or not actual_cost_gid:
            logger.warning(f"Could not find custom field GIDs for project {project_id}")
            return False
        
        # Get all tasks in the project
        tasks = client.tasks.find_by_project(project_id)
        
        total_estimated = 0
        total_actual = 0
        completed_tasks = 0
        total_tasks = 0
        overbudget_tasks = []
        
        # Find status task or create if not exists
        status_task_gid = find_status_task(project_id)
        if not status_task_gid:
            status_task_gid = create_status_task(project_id)
            if not status_task_gid:
                return False
        
        # Process each task
        for task in tasks:
            task_gid = task['gid']
            
            # Skip the status task itself
            if task_gid == status_task_gid:
                continue
            
            # Get full task details to access custom fields
            task_details = client.tasks.find_by_id(task_gid)
            
            total_tasks += 1
            
            # Extract costs from custom fields
            estimated_cost = 0
            actual_cost = 0
            
            if 'custom_fields' in task_details:
                for field in task_details['custom_fields']:
                    if field['gid'] == estimated_cost_gid and field.get('number_value') is not None:
                        estimated_cost = field['number_value']
                    elif field['gid'] == actual_cost_gid and field.get('number_value') is not None:
                        actual_cost = field['number_value']
            
            # Add to totals
            total_estimated += estimated_cost
            
            # Only add actual costs if they exist (work completed)
            if actual_cost > 0:
                total_actual += actual_cost
                completed_tasks += 1
                
                # Check if task is over budget
                if actual_cost > estimated_cost:
                    overbudget_tasks.append({
                        'name': task['name'],
                        'estimated': estimated_cost,
                        'actual': actual_cost,
                        'difference': actual_cost - estimated_cost
                    })
        
        # Create summary
        percent_complete = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0
        budget_progress = (total_actual / total_estimated * 100) if total_estimated > 0 else 0
        
        # Get project name
        project_info = client.projects.find_by_id(project_id)
        project_name = project_info.get('name', 'Construction Project')
        
        summary = f"""# 🏗️ {project_name} Budget Summary

## 💰 Overall Budget
- 💵 Total Estimated Budget: ${total_estimated:.2f}
- 💸 Total Actual Cost Incurred: ${total_actual:.2f}
- 🎯 Remaining Budget: ${total_estimated - total_actual:.2f}
- 📊 Budget Utilization: {budget_progress:.1f}%

## 📋 Progress
- 📝 Total Tasks: {total_tasks}
- ✅ Completed Tasks (with actual costs): {completed_tasks}
- 🚧 Project Completion: {percent_complete:.1f}%

"""
        
        # Add overbudget section if there are overbudget tasks
        if overbudget_tasks:
            summary += "## ⚠️ Overbudget Items\n"
            for item in overbudget_tasks:
                summary += f"- ❗ {item['name']}: Estimated ${item['estimated']:.2f}, Actual ${item['actual']:.2f} (${item['difference']:.2f} over budget)\n"
            
            total_overbudget = sum(item['difference'] for item in overbudget_tasks)
            summary += f"\n⚠️ Total Amount Over Budget: ${total_overbudget:.2f}\n"
        
        # Add last updated timestamp
        summary += f"\n\n🕒 Last Updated: {current_time}"
        
        # Update the status task
        client.tasks.update(status_task_gid, {
            'notes': summary
        })
        
        logger.info(f"Successfully updated project metrics for project {project_id}")
        return True
        
    except Exception as e:
        logger.error(f"Error updating project metrics for project {project_id}: {e}")
        return False

def determine_projects_to_update(event_data=None):
    """Determine which projects to update based on webhook event or manual trigger"""
    projects_to_update = []
    
    if event_data and 'events' in event_data:
        # Extract project IDs from the webhook event
        for event in event_data['events']:
            if 'resource' in event and event.get('resource', {}).get('resource_type') == 'task':
                task_gid = event.get('resource', {}).get('gid')
                if task_gid:
                    # Get the projects this task belongs to
                    try:
                        task = client.tasks.find_by_id(task_gid)
                        if 'projects' in task:
                            for project in task['projects']:
                                project_id = project['gid']
                                if project_id not in projects_to_update:
                                    # Verify this project has our custom fields
                                    estimated, actual = get_custom_fields(project_id)
                                    if estimated and actual:
                                        projects_to_update.append(project_id)
                    except Exception as e:
                        logger.error(f"Error getting task details for task {task_gid}: {e}")
    
    # If no projects were found from the event, or this is a manual trigger
    if not projects_to_update:
        # Get the workspace of our template project
        workspace_id = get_project_workspace(TEMPLATE_PROJECT_ID)
        if workspace_id:
            # Get all projects in the workspace
            all_projects = get_all_projects_in_workspace(workspace_id)
            
            # Check each project for our custom fields
            for project in all_projects:
                project_id = project['gid']
                estimated, actual = get_custom_fields(project_id)
                if estimated and actual:
                    projects_to_update.append(project_id)
    
    return projects_to_update

@app.route('/webhook', methods=['POST'])
def handle_webhook():
    """Handles incoming webhook requests from Asana"""
    # Check if this is the webhook handshake request
    if 'X-Hook-Secret' in request.headers:
        secret = request.headers['X-Hook-Secret']
        WEBHOOK_SECRET['secret'] = secret  # Store secret dynamically
        
        response = jsonify({})
        response.headers['X-Hook-Secret'] = secret  # Send back the secret
         
        logger.info(f"Webhook Handshake Successful. Secret: {secret}")
        return response, 200
    
    # If it's not a handshake, it's an event
    try:
        # Get the request data
        event_data = request.json
        logger.info(f"Received webhook event: {event_data}")
        
        # Determine which projects to update
        projects_to_update = determine_projects_to_update(event_data)
        
        update_results = {}
        for project_id in projects_to_update:
            logger.info(f"Updating project metrics for project {project_id}")
            success = update_project_metrics(project_id)
            update_results[project_id] = success
            
        return jsonify({
            "status": "received", 
            "updated_projects": update_results
        }), 200
    except Exception as e:
        logger.error(f"Error processing webhook: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500
        
@app.route('/setup', methods=['GET'])
def setup():
    """Setup endpoint to initialize all project status tasks and metrics"""
    projects_to_update = determine_projects_to_update()
    
    results = {}
    for project_id in projects_to_update:
        # Find or create status task
        status_task_gid = find_status_task(project_id)
        if not status_task_gid:
            status_task_gid = create_status_task(project_id)
        
        # Update metrics
        success = update_project_metrics(project_id)
        results[project_id] = success
    
    if any(results.values()):
        return jsonify({
            "status": "success", 
            "message": "Project status tasks created and metrics updated",
            "results": results
        }), 200
    else:
        return jsonify({
            "status": "error", 
            "message": "Failed to setup project status",
            "results": results
        }), 500

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({"status": "healthy"}), 200

@app.route('/register-webhook', methods=['GET'])
def register_webhook():
    """Register webhooks for all projects"""
    try:
        # Force HTTPS for Railway app URL
        webhook_url = "https://asanaconnector2claude-production.up.railway.app/webhook"
        
        # Get workspace ID from template project
        workspace_id = get_project_workspace(TEMPLATE_PROJECT_ID)
        if not workspace_id:
            return jsonify({
                "status": "error",
                "message": "Could not determine workspace ID"
            }), 500
            
        # Register a webhook for the workspace instead of individual projects
        webhook = client.webhooks.create({
            'resource': workspace_id,
            'target': webhook_url
        })
        
        logger.info(f"Webhook registered for workspace {workspace_id}: {webhook['gid']}")
        return jsonify({
            "status": "success", 
            "message": f"Webhook registered for workspace {workspace_id}", 
            "webhook_gid": webhook['gid'],
            "target_url": webhook_url
        }), 200
        
    except Exception as e:
        logger.error(f"Error registering webhook: {e}")
        return jsonify({
            "status": "error", 
            "message": f"Failed to register webhook: {str(e)}"
        }), 500

@app.route('/update', methods=['GET'])
def manual_update():
    """Manually trigger an update of all Project Status tasks"""
    projects_to_update = determine_projects_to_update()
    
    results = {}
    for project_id in projects_to_update:
        success = update_project_metrics(project_id)
        results[project_id] = success
    
    if any(results.values()):
        return jsonify({
            "status": "success", 
            "message": "Project statuses manually updated",
            "results": results
        }), 200
    else:
        return jsonify({
            "status": "error", 
            "message": "Failed to update project statuses",
            "results": results
        }), 500

@app.route('/update-status', methods=['GET'])
def update_status():
    """User-friendly endpoint to manually update all Project Status tasks"""
    try:
        projects_to_update = determine_projects_to_update()
        
        results = {}
        for project_id in projects_to_update:
            success = update_project_metrics(project_id)
            
            # Get project name for display
            try:
                project_info = client.projects.find_by_id(project_id)
                project_name = project_info.get('name', f'Project {project_id}')
            except:
                project_name = f'Project {project_id}'
                
            results[project_id] = {
                'success': success,
                'name': project_name
            }
        
        # Count successful updates
        successful_updates = sum(1 for result in results.values() if result['success'])
        
        # Return an HTML page with results
        html_response = """
        <html>
        <head>
            <title>Project Status Updated</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 40px; line-height: 1.6; }
                .success { color: green; font-weight: bold; }
                .error { color: red; font-weight: bold; }
                .container { max-width: 800px; margin: 0 auto; }
                h1 { color: #333; }
                .button { 
                    display: inline-block; 
                    background: #4CAF50; 
                    color: white; 
                    padding: 10px 20px; 
                    text-decoration: none; 
                    border-radius: 4px; 
                    margin-top: 20px; 
                }
                table { width: 100%; border-collapse: collapse; margin-top: 20px; }
                th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
                th { background-color: #f2f2f2; }
                tr:nth-child(even) { background-color: #f9f9f9; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Project Status Update Results</h1>
        """
        
        if successful_updates > 0:
            html_response += f"""
                <p class="success">✅ Successfully updated {successful_updates} project(s)!</p>
            """
        else:
            html_response += """
                <p class="error">❌ Failed to update any projects.</p>
            """
        
        html_response += f"""
                <p>Current time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
                
                <h2>Update Details</h2>
                <table>
                    <tr>
                        <th>Project</th>
                        <th>Status</th>
                    </tr>
        """
        
        # Add rows for each project
        for project_id, result in results.items():
            status_text = "✅ Updated" if result['success'] else "❌ Failed"
            html_response += f"""
                    <tr>
                        <td>{result['name']}</td>
                        <td>{status_text}</td>
                    </tr>
            """
        
        html_response += """
                </table>
                
                <a href="/update-status" class="button">Update Again</a>
            </div>
        </body>
        </html>
        """
        
        return html_response
        
    except Exception as e:
        logger.error(f"Error in update-status endpoint: {e}")
        return f"Error updating Project Status: {str(e)}", 500

if __name__ == '__main__':
    app.run(debug=True)