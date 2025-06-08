# Expense Tracker

A simple and intuitive web application to track your daily expenses. Built with Flask and SQLite.

## Features

- User authentication (register/login)
- Add new expenses with description, amount, category, and date
- View all expenses in a clean, organized table
- Track total expenses, number of transactions, and average transaction amount
- Categorize expenses for better organization
- Responsive design that works on all devices

## Setup Instructions

1. Clone this repository or download the files
2. Create a virtual environment (recommended):
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```
3. Install the required packages:
   ```bash
   pip install -r requirements.txt
   ```
4. Run the application:
   ```bash
   python app.py
   ```
5. Open your web browser and navigate to `http://localhost:5000`

## Usage

1. Register a new account or login if you already have one
2. Click "Add Expense" to add a new expense
3. Fill in the expense details (description, amount, category, and date)
4. View your expenses and statistics on the dashboard
5. Use the navigation bar to add more expenses or logout

## Technologies Used

- Python 3.x
- Flask
- SQLAlchemy
- SQLite
- Bootstrap 5
- Flask-Login

## Security Note

This is a basic implementation for learning purposes. In a production environment, you should:

- Use proper password hashing (e.g., with Werkzeug's `generate_password_hash`)
- Implement CSRF protection
- Use environment variables for sensitive data
- Add input validation and sanitization
- Use HTTPS
- Implement proper session management 