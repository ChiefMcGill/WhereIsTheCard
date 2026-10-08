from flask_wtf import FlaskForm
from wtforms import PasswordField, StringField, SubmitField, IntegerField
from wtforms.validators import DataRequired, Email, Length, Optional


class LoginForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField("Password", validators=[DataRequired()])
    submit = SubmitField("Log in")


class CheckoutForm(FlaskForm):
    card_id = IntegerField("Card", validators=[DataRequired()])
    purpose = StringField("Purpose", validators=[DataRequired(), Length(min=2, max=300)])
    duration_minutes = IntegerField("Expected duration", validators=[Optional()])
    custom_duration_minutes = IntegerField("Custom duration", validators=[Optional()])
    submit = SubmitField("Check out")
