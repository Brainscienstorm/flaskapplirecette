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


@app.route('/surprise')
def surprise_me():
    conn = get_db_connection()
    recipe = conn.execute('''
        SELECT r.*, GROUP_CONCAT(DISTINCT t.nom_type_{0}) as types, 
        GROUP_CONCAT(DISTINCT c.nom_categorie_{0}) as categories,
        r.pays_origine_{0} as country_of_origin
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
        return render_template("no_recipes.html")  # Handle the case if no recipe is found
    
    ingredients = conn.execute('''
        SELECT i.nom_ingredient_{0} as ingredient, iq.quantity_{0} as quantity
        FROM Ingredients i
        JOIN RecetteIngredients ri ON i.id = ri.ingredient_id
        LEFT JOIN IngredientQuantities iq ON i.id = iq.ingredient_id AND ri.recette_id = iq.recette_id
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



@app.route('/guided', methods=['GET', 'POST'])
def guided_choice():
    conn = get_db_connection()
    
    # Fetch available categories, types, countries, and preparation times
    categories = conn.execute('''SELECT DISTINCT nom_categorie_{0} as category FROM Categories'''.format(session['lang'])).fetchall()
    types = conn.execute('''SELECT DISTINCT nom_type_{0} as type FROM Types'''.format(session['lang'])).fetchall()
    countries = conn.execute('''SELECT DISTINCT pays_origine_{0} as country FROM Recettes'''.format(session['lang'])).fetchall()

    # Fetch distinct preparation times and categorize into less than and more than 1 hour
    prep_times = conn.execute('''
        SELECT DISTINCT 
            CASE 
                WHEN temps_preparation_{0} LIKE "%h%" OR temps_preparation_{0} LIKE "%hour%" THEN "more_than_1h"
                ELSE "less_than_1h"
            END as prep_time_category
        FROM Recettes
    '''.format(session['lang'])).fetchall()

    if request.method == 'POST':
        # Get the selected filters from the form
        category = request.form['category']
        dish_type = request.form['type']
        country = request.form['country']
        prep_time = request.form['prep_time']

        # Construct the SQL query based on the selected filters
        query = '''SELECT r.*, GROUP_CONCAT(DISTINCT t.nom_type_{0}) as types, 
                          GROUP_CONCAT(DISTINCT c.nom_categorie_{0}) as categories,
                          r.pays_origine_{0} as country_of_origin
                   FROM Recettes r
                   JOIN RecetteCategories rc ON r.id = rc.recette_id
                   JOIN Categories c ON rc.categorie_id = c.id
                   JOIN RecetteTypes rt ON r.id = rt.recette_id
                   JOIN Types t ON rt.type_id = t.id
                   WHERE 1=1'''.format(session['lang'])

        # Apply filters if they are not set to "any"
        filters = []
        if category != 'any':
            query += " AND c.nom_categorie_{0} = ?".format(session['lang'])
            filters.append(category)
        if dish_type != 'any':
            query += " AND t.nom_type_{0} = ?".format(session['lang'])
            filters.append(dish_type)
        if country != 'any':
            query += " AND r.pays_origine_{0} = ?".format(session['lang'])
            filters.append(country)
        if prep_time != 'any':
            query += " AND CASE WHEN temps_preparation_{0} LIKE '%h%' OR temps_preparation_{0} LIKE '%hour%' THEN 'more_than_1h' ELSE 'less_than_1h' END = ?".format(session['lang'])
            filters.append(prep_time)

        query += " GROUP BY r.id ORDER BY RANDOM() LIMIT 10"
        
        recipes_raw = conn.execute(query, tuple(filters)).fetchall()

        # Convert the query results to a list of dictionaries for session storage
        recipes = [dict(recipe) for recipe in recipes_raw]
        
        if recipes:
            # Store the filtered results and initial index in the session
            session['guided_choice_results'] = recipes
            session['guided_choice_index'] = 0
            session['selected_category'] = category
            session['selected_type'] = dish_type
            session['selected_country'] = country
            session['selected_prep_time'] = prep_time
            conn.close()
            return redirect(url_for('guided_choice'))
        else:
            conn.close()
            return render_template('guided.html', categories=categories, types=types, countries=countries,
                                   prep_times=prep_times, no_recipe=True)

    else:
        # Handle the GET request, where the results are already stored in session
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
            
            # Fetch ingredients and instructions for the current recipe
            ingredients = conn.execute('''
                SELECT i.nom_ingredient_{0} as ingredient, iq.quantity_{0} as quantity
                FROM Ingredients i
                JOIN RecetteIngredients ri ON i.id = ri.ingredient_id
                LEFT JOIN IngredientQuantities iq ON i.id = iq.ingredient_id AND ri.recette_id = iq.recette_id
                WHERE ri.recette_id = ?
            '''.format(session['lang']), (current_recipe['id'],)).fetchall()

            instructions = conn.execute('''
                SELECT step_number, instruction_{0} as instruction 
                FROM Instructions 
                WHERE recette_id = ?
                ORDER BY step_number
            '''.format(session['lang']), (current_recipe['id'],)).fetchall()
            
            conn.close()
            return render_template('guided.html', categories=categories, types=types, countries=countries,
                                   prep_times=prep_times, recipe=current_recipe, ingredients=ingredients,
                                   instructions=instructions, current_index=index, total=len(recipes))
        else:
            conn.close()
            return render_template('guided.html', categories=categories, types=types, countries=countries,
                                   prep_times=prep_times)





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



@app.route('/ingredient_search', methods=['GET', 'POST'])
def ingredient_search():
    conn = get_db_connection()
    
    # 1. Fetch distinct types for the dropdown
    types = conn.execute('''
        SELECT DISTINCT nom_type_{0} AS type
        FROM Types
        ORDER BY 1
    '''.format(session['lang'])).fetchall()

    # (Optional) for autocomplete, if you still need it in the template
    ingredients_autocomplete = conn.execute('''
        SELECT nom_ingredient_{0} as ingredient 
        FROM Ingredients
    '''.format(session['lang'])).fetchall()

    if request.method == 'POST':
        # 2. Capture ingredients and type from the form
        user_ingredients = [i.strip() for i in request.form.get('ingredients', '').split(',')]
        selected_type = request.form.get('type', 'any')

        # 3. Query all recipes + their ingredients + their types
        all_recipes = conn.execute('''
            SELECT r.id,
                   r.plat_{0} AS name,
                   GROUP_CONCAT(DISTINCT i.nom_ingredient_{0}) AS ingredients,
                   GROUP_CONCAT(DISTINCT t.nom_type_{0}) AS types
            FROM Recettes r
            JOIN RecetteIngredients ri ON r.id = ri.recette_id
            JOIN Ingredients i        ON ri.ingredient_id = i.id
            LEFT JOIN RecetteTypes rt ON r.id = rt.recette_id
            LEFT JOIN Types t         ON rt.type_id = t.id
            GROUP BY r.id
        '''.format(session['lang'])).fetchall()

        recipes = []
        for recipe in all_recipes:
            if not recipe['ingredients']:
                continue
            recipe_ingredients = {ing.strip() for ing in recipe['ingredients'].split(',')}
            user_ingredients_set = set(user_ingredients)

            # Check if there's at least some overlap in ingredients
            matches = recipe_ingredients.intersection(user_ingredients_set)
            if matches:
                # If user chose a specific type, check if it's present
                if selected_type != 'any':
                    # 'types' might be None if no type is associated; handle that
                    if recipe['types']:
                        recipe_types = {t.strip() for t in recipe['types'].split(',')}
                        if selected_type in recipe_types:
                            # Calculate your match percentage or any other metric
                            match_percent = len(matches) / len(recipe_ingredients) * 100
                            recipes.append({
                                'id': recipe['id'],
                                'name': recipe['name'],
                                'match_percent': match_percent
                            })
                    # else: no types at all, skip
                else:
                    # 'any' means we don't filter by type
                    match_percent = len(matches) / len(recipe_ingredients) * 100
                    recipes.append({
                        'id': recipe['id'],
                        'name': recipe['name'],
                        'match_percent': match_percent
                    })

        # 4. Sort by match percentage (highest first) and limit to 10
        recipes.sort(key=lambda x: x['match_percent'], reverse=True)
        recipes = recipes[:10]
        
        # 5. Store results & selections in session so we can navigate them
        session['ingredient_search_results'] = recipes
        session['ingredient_search_index'] = 0
        session['ingredient_search_terms'] = user_ingredients
        session['selected_type'] = selected_type
        
        conn.close()
        return redirect(url_for('ingredient_search'))
    
    else:
        # ----- GET request -----
        selected_type = session.get('selected_type', 'any')
        if 'ingredient_search_results' in session:
            action = request.args.get('action')
            index = session.get('ingredient_search_index', 0)
            recipes = session['ingredient_search_results']
            
            # Handle next/prev
            if action == 'next' and index < len(recipes) - 1:
                index += 1
            elif action == 'prev' and index > 0:
                index -= 1
            session['ingredient_search_index'] = index
            
            # Retrieve current recipe details
            current_recipe = recipes[index]
            top_recipe = conn.execute(''' 
                SELECT r.*,
                       GROUP_CONCAT(DISTINCT t.nom_type_{0})         as types, 
                       GROUP_CONCAT(DISTINCT c.nom_categorie_{0})    as categories,
                       r.pays_origine_{0}                            as country_of_origin
                FROM Recettes r
                LEFT JOIN RecetteTypes rt     ON r.id = rt.recette_id
                LEFT JOIN Types t             ON rt.type_id = t.id
                LEFT JOIN RecetteCategories rc ON r.id = rc.recette_id
                LEFT JOIN Categories c        ON rc.categorie_id = c.id
                WHERE r.id = ?
                GROUP BY r.id
            '''.format(session['lang']), (current_recipe['id'],)).fetchone()

            details_ingredients = conn.execute('''
                SELECT i.nom_ingredient_{0} as ingredient,
                       iq.quantity_{0}       as quantity
                FROM Ingredients i
                JOIN RecetteIngredients ri
                  ON i.id = ri.ingredient_id
                LEFT JOIN IngredientQuantities iq
                  ON i.id = iq.ingredient_id
                 AND ri.recette_id = iq.recette_id
                WHERE ri.recette_id = ?
            '''.format(session['lang']), (current_recipe['id'],)).fetchall()

            instructions = conn.execute('''
                SELECT step_number,
                       instruction_{0} as instruction 
                FROM Instructions 
                WHERE recette_id = ?
                ORDER BY step_number
            '''.format(session['lang']), (current_recipe['id'],)).fetchall()
            
            conn.close()
            return render_template('ingredient_search.html',
                                   ingredients_list=ingredients_autocomplete,
                                   types=types,
                                   selected_type=selected_type,
                                   top_recipe=top_recipe,
                                   ingredients_details=details_ingredients,
                                   instructions=instructions,
                                   search_terms=session.get('ingredient_search_terms', []),
                                   current_index=index,
                                   total=len(recipes))
        else:
            # No search done yet
            conn.close()
            return render_template('ingredient_search.html',
                                   ingredients_list=ingredients_autocomplete,
                                   types=types,
                                   selected_type='any')




if __name__ == '__main__':
    app.run(debug=True)