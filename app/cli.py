import shutil
from datetime import datetime

import click

from app import create_app
from app.extensions import db
from app.models import Card, User


@click.group()
def cli():
    """Utility commands for Where Is The Card."""


@cli.command()
def create_admin():
    app = create_app()
    with app.app_context():
        name = click.prompt("Name")
        email = click.prompt("Email").strip().lower()
        password = click.prompt("Password", hide_input=True, confirmation_prompt=True)

        if User.query.filter_by(email=email).first():
            raise click.ClickException("A user with that email already exists.")

        user = User(name=name, email=email, role="ADMIN", active=True)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Admin user created: {user.email}")


@cli.command()
def create_user():
    app = create_app()
    with app.app_context():
        name = click.prompt("Name")
        email = click.prompt("Email").strip().lower()
        role = click.prompt("Role", default="USER", show_default=True)
        password = click.prompt("Password", hide_input=True, confirmation_prompt=True)

        if User.query.filter_by(email=email).first():
            raise click.ClickException("A user with that email already exists.")

        user = User(name=name, email=email, role=role.upper(), active=True)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"User created: {user.email} ({user.role})")


@cli.command()
@click.argument("email")
def disable_user(email):
    app = create_app()
    with app.app_context():
        user = User.query.filter_by(email=email.lower()).first()
        if not user:
            raise click.ClickException("User not found.")
        user.active = False
        db.session.commit()
        click.echo(f"User disabled: {user.email}")


@cli.command()
@click.argument("email")
def reset_password(email):
    app = create_app()
    with app.app_context():
        user = User.query.filter_by(email=email.lower()).first()
        if not user:
            raise click.ClickException("User not found.")
        password = click.prompt("New password", hide_input=True, confirmation_prompt=True)
        user.set_password(password)
        db.session.commit()
        click.echo(f"Password reset for {user.email}")


@cli.command()
def list_users():
    app = create_app()
    with app.app_context():
        users = User.query.order_by(User.name).all()
        if not users:
            click.echo("No users found.")
            return
        for user in users:
            click.echo(f"{user.id} | {user.name} | {user.email} | {user.role} | active={user.active}")


@cli.command()
def backup():
    app = create_app()
    with app.app_context():
        src = "//data/app.db"
        dest = "//data/app.db.backup"
        shutil.copy2(src, dest)
        click.echo(f"Backup created at {dest}")


if __name__ == "__main__":
    cli()
