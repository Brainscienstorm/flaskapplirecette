from flask import Flask, render_template, request, session, redirect, url_for, jsonify
import sqlite3
import random
from collections import defaultdict

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'  # Change this for production

DATABASE = 'recettes.db'

# Helper functions
def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn



# Language handling
@app.before_request
def set_default_language():
    if 'lang' not in session:
        session['lang'] = 'en'

# Main routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/set_language/<lang>')
def set_language(lang):
    session['lang'] = lang if lang in ['en', 'fr'] else 'en'
    # Clear session variables related to other functionalities:
    session.pop('ingredient_search_results', None)
    session.pop('ingredient_search_index', None)
    session.pop('ingredient_search_terms', None)
    session.pop('guided_choice_results', None)
    session.pop('guided_choice_index', None)
    session.pop('selected_category', None)
    session.pop('selected_type', None)
    return redirect(url_for('index'))


# Surprise Me functionality
@app.route('/surprise')
def surprise_me():
    conn = get_db_connection()
    recipe = conn.execute('''
        SELECT r.*, GROUP_CONCAT(DISTINCT t.nom_type_{0}) as types, 
        GROUP_CONCAT(DISTINCT c.nom_categorie_{0}) as categories
        FROM Recettes r
        LEFT JOIN RecetteTypes rt ON r.id = rt.recette_id
        LEFT JOIN Types t ON rt.type_id = t.id
        LEFT JOIN RecetteCategories rc ON r.id = rc.recette_id
        LEFT JOIN Categories c ON rc.categorie_id = c.id
        GROUP BY r.id
        ORDER BY RANDOM()
        LIMIT 1
    '''.format(session['lang'])).fetchone()
    if recipe is None:
        # Handle the case where no recipes are in the database
        # e.g., redirect or show a message
        return render_template("no_recipes.html")  # or something appropriate

    
    ingredients = conn.execute('''
        SELECT i.nom_ingredient_{0} as ingredient 
        FROM Ingredients i
        JOIN RecetteIngredients ri ON i.id = ri.ingredient_id
        WHERE ri.recette_id = ?
    '''.format(session['lang']), (recipe['id'],)).fetchall()
    
    instructions = conn.execute('''
        SELECT step_number, instruction_{0} as instruction 
        FROM Instructions 
        WHERE recette_id = ?
        ORDER BY step_number
    '''.format(session['lang']), (recipe['id'],)).fetchall()
    
    conn.close()
    return render_template('surprise.html', recipe=recipe, ingredients=ingredients, instructions=instructions)

# Guided Choice functionality (updated for next/previous navigation)
@app.route('/guided', methods=['GET', 'POST'])
def guided_choice():
    conn = get_db_connection()
    
    # Get available categories and types
    categories = conn.execute('''
        SELECT DISTINCT nom_categorie_{0} as category 
        FROM Categories
    '''.format(session['lang'])).fetchall()
    
    types = conn.execute('''
        SELECT DISTINCT nom_type_{0} as type 
        FROM Types
    '''.format(session['lang'])).fetchall()
    
    if request.method == 'POST':
        category = request.form['category']
        dish_type = request.form['type']
        
        # Query for matching recipes, now retrieving up to 10 recipes
        recipes_raw = conn.execute('''
            SELECT r.*, GROUP_CONCAT(DISTINCT t.nom_type_{0}) as types, 
                   GROUP_CONCAT(DISTINCT c.nom_categorie_{0}) as categories
            FROM Recettes r
            JOIN RecetteCategories rc ON r.id = rc.recette_id
            JOIN Categories c ON rc.categorie_id = c.id
            JOIN RecetteTypes rt ON r.id = rt.recette_id
            JOIN Types t ON rt.type_id = t.id
            WHERE c.nom_categorie_{0} = ? AND t.nom_type_{0} = ?
            GROUP BY r.id
            ORDER BY RANDOM()
            LIMIT 10
        '''.format(session['lang']), (category, dish_type)).fetchall()
        
        # Convert the results to a list of dictionaries for session storage
        recipes = [dict(recipe) for recipe in recipes_raw]
        
        if recipes:
            # Store results and initial index in the session
            session['guided_choice_results'] = recipes
            session['guided_choice_index'] = 0
            session['selected_category'] = category
            session['selected_type'] = dish_type
            conn.close()
            return redirect(url_for('guided_choice'))
        else:
            conn.close()
            # Render a message if no recipe was found for the given criteria.
            return render_template('guided.html', categories=categories, types=types, 
                                   selected_category=category, selected_type=dish_type, 
                                   no_recipe=True)
    else:
        # GET request: check if we have guided choice results stored in session
        if 'guided_choice_results' in session:
            recipes = session['guided_choice_results']
            index = session.get('guided_choice_index', 0)
            action = request.args.get('action')
            if action == 'next' and index < len(recipes) - 1:
                index += 1
            elif action == 'prev' and index > 0:
                index -= 1
            session['guided_choice_index'] = index
            current_recipe = recipes[index]
            
            # Fetch ingredients for the current recipe
            ingredients = conn.execute('''
                SELECT i.nom_ingredient_{0} as ingredient 
                FROM Ingredients i
                JOIN RecetteIngredients ri ON i.id = ri.ingredient_id
                WHERE ri.recette_id = ?
            '''.format(session['lang']), (current_recipe['id'],)).fetchall()
            
            # Fetch instructions for the current recipe
            instructions = conn.execute('''
                SELECT step_number, instruction_{0} as instruction 
                FROM Instructions 
                WHERE recette_id = ?
                ORDER BY step_number
            '''.format(session['lang']), (current_recipe['id'],)).fetchall()
            
            conn.close()
            return render_template('guided.html', categories=categories, types=types, 
                                   selected_category=session.get('selected_category'),
                                   selected_type=session.get('selected_type'),
                                   recipe=current_recipe, ingredients=ingredients,
                                   instructions=instructions,
                                   current_index=index, total=len(recipes))
        else:
            conn.close()
            return render_template('guided.html', categories=categories, types=types)




@app.route('/ingredient_suggestions')
def ingredient_suggestions():
    term = request.args.get('term', '')
    lang = session.get('lang', 'en')  # default to 'en' if not set
    conn = get_db_connection()
    suggestions = conn.execute('''
        SELECT nom_ingredient_{0} as ingredient
        FROM Ingredients
        WHERE nom_ingredient_{0} LIKE ?
        GROUP BY ingredient
        LIMIT 10
    '''.format(lang), ('%' + term + '%',)).fetchall()
    conn.close()
    
    # Extract the ingredient values from the rows
    suggestion_list = [row['ingredient'] for row in suggestions]
    return jsonify(suggestion_list)



# Ingredient Search functionality (updated for one-at-a-time navigation)
@app.route('/ingredient_search', methods=['GET', 'POST'])
def ingredient_search():
    conn = get_db_connection()
    
    # (Optional) Get autocomplete suggestions – you can remove this if you prefer not to show a list.
    ingredients_autocomplete = conn.execute('''
        SELECT nom_ingredient_{0} as ingredient 
        FROM Ingredients
    '''.format(session['lang'])).fetchall()

    if request.method == 'POST':
        # Process the submitted search form
        user_ingredients = [i.strip() for i in request.form.get('ingredients', '').split(',')]
        
        # Find matching recipes
        recipes = []
        all_recipes = conn.execute('''
            SELECT r.id, r.plat_{0} as name, 
                   GROUP_CONCAT(DISTINCT i.nom_ingredient_{0}) as ingredients
            FROM Recettes r
            JOIN RecetteIngredients ri ON r.id = ri.recette_id
            JOIN Ingredients i ON ri.ingredient_id = i.id
            GROUP BY r.id
        '''.format(session['lang'])).fetchall()
        
        for recipe in all_recipes:
            recipe_ingredients = set(recipe['ingredients'].split(','))
            user_ingredients_set = set(user_ingredients)
            matches = recipe_ingredients.intersection(user_ingredients_set)
            if matches:
                recipes.append({
                    'id': recipe['id'],
                    'name': recipe['name'],
                    'match_percent': len(matches) / len(recipe_ingredients) * 100
                })
        
        # Sort by match percentage (highest first) and limit to a maximum of 10 suggestions
        recipes.sort(key=lambda x: x['match_percent'], reverse=True)
        recipes = recipes[:10]
        
        # Store the search results and initial index in the session
        session['ingredient_search_results'] = recipes
        session['ingredient_search_index'] = 0
        session['ingredient_search_terms'] = user_ingredients
        
        conn.close()
        # Redirect to the GET route to display the first suggestion
        return redirect(url_for('ingredient_search'))
    
    else:  # GET request
        if 'ingredient_search_results' in session:
            action = request.args.get('action')
            index = session.get('ingredient_search_index', 0)
            recipes = session['ingredient_search_results']
            
            # Adjust the index based on the action parameter (next or previous)
            if action == 'next' and index < len(recipes) - 1:
                index += 1
            elif action == 'prev' and index > 0:
                index -= 1
            session['ingredient_search_index'] = index
            
            # Retrieve the current recipe details
            current_recipe = recipes[index]
            top_recipe = conn.execute('''
                SELECT r.*, GROUP_CONCAT(DISTINCT t.nom_type_{0}) as types, 
                       GROUP_CONCAT(DISTINCT c.nom_categorie_{0}) as categories
                FROM Recettes r
                LEFT JOIN RecetteTypes rt ON r.id = rt.recette_id
                LEFT JOIN Types t ON rt.type_id = t.id
                LEFT JOIN RecetteCategories rc ON r.id = rc.recette_id
                LEFT JOIN Categories c ON rc.categorie_id = c.id
                WHERE r.id = ?
                GROUP BY r.id
            '''.format(session['lang']), (current_recipe['id'],)).fetchone()
            
            details_ingredients = conn.execute('''
                SELECT i.nom_ingredient_{0} as ingredient 
                FROM Ingredients i
                JOIN RecetteIngredients ri ON i.id = ri.ingredient_id
                WHERE ri.recette_id = ?
            '''.format(session['lang']), (current_recipe['id'],)).fetchall()
            
            instructions = conn.execute('''
                SELECT step_number, instruction_{0} as instruction 
                FROM Instructions 
                WHERE recette_id = ?
                ORDER BY step_number
            '''.format(session['lang']), (current_recipe['id'],)).fetchall()
            
            conn.close()
            return render_template('ingredient_search.html',
                                   ingredients_list=ingredients_autocomplete,
                                   top_recipe=top_recipe,
                                   ingredients_details=details_ingredients,
                                   instructions=instructions,
                                   search_terms=session.get('ingredient_search_terms', []),
                                   current_index=index,
                                   total=len(recipes))
        else:
            conn.close()
            # No search has been performed yet; show the search form.
            return render_template('ingredient_search.html', ingredients_list=ingredients_autocomplete)

if __name__ == '__main__':
    app.run(debug=True)