"""esquema inicial: cuentas, menu semanal, recetas y datos del beta

Revision ID: 87669eb0db17
Revises: 
Create Date: 2026-08-01 16:30:31.179531

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '87669eb0db17'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """El esquema completo: cuentas, perfiles, menús semanales, recetas y BETA."""
    op.create_table('app_events',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=True),
    sa.Column('user_id', sa.Uuid(), nullable=True),
    sa.Column('name', sa.String(length=60), nullable=False),
    sa.Column('props', sa.JSON(), nullable=False),
    sa.Column('session_id', sa.String(length=64), nullable=True),
    sa.Column('platform', sa.String(length=20), nullable=True),
    sa.Column('app_version', sa.String(length=20), nullable=True),
    sa.Column('at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_app_events_name_at', 'app_events', ['name', 'at'], unique=False)
    op.create_index(op.f('ix_app_events_tenant_id'), 'app_events', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_app_events_user_id'), 'app_events', ['user_id'], unique=False)
    op.create_table('audit_log',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('actor_id', sa.Uuid(), nullable=True),
    sa.Column('action', sa.String(length=60), nullable=False),
    sa.Column('entity_type', sa.String(length=40), nullable=False),
    sa.Column('entity_id', sa.Uuid(), nullable=False),
    sa.Column('details', sa.JSON(), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_audit_log_tenant_id'), 'audit_log', ['tenant_id'], unique=False)
    op.create_table('dish_recipes',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('dish_key', sa.String(length=64), nullable=False),
    sa.Column('template_id', sa.String(length=60), nullable=True),
    sa.Column('name_es', sa.String(length=160), nullable=False),
    sa.Column('ingredients', sa.JSON(), nullable=False),
    sa.Column('steps', sa.JSON(), nullable=False),
    sa.Column('prep_minutes', sa.SmallInteger(), nullable=True),
    sa.Column('difficulty', sa.String(length=20), nullable=True),
    sa.Column('tips', sa.Text(), nullable=True),
    sa.Column('source', sa.String(length=10), nullable=False),
    sa.Column('model', sa.String(length=60), nullable=True),
    sa.Column('prompt_version', sa.String(length=60), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_dish_recipes_dish_key'), 'dish_recipes', ['dish_key'], unique=True)
    op.create_index(op.f('ix_dish_recipes_template_id'), 'dish_recipes', ['template_id'], unique=False)
    op.create_table('foods',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=True),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('source_ref', sa.String(length=50), nullable=True),
    sa.Column('name_es', sa.String(length=200), nullable=False),
    sa.Column('name_norm', sa.String(length=200), nullable=False),
    sa.Column('name_en', sa.String(length=200), nullable=True),
    sa.Column('category', sa.String(length=20), nullable=False),
    sa.Column('kcal_100g', sa.Float(), nullable=False),
    sa.Column('protein_100g', sa.Float(), nullable=False),
    sa.Column('carb_100g', sa.Float(), nullable=False),
    sa.Column('fat_100g', sa.Float(), nullable=False),
    sa.Column('fiber_100g', sa.Float(), server_default='0', nullable=False),
    sa.Column('tags', sa.JSON(), nullable=False),
    sa.Column('aliases', sa.JSON(), server_default='[]', nullable=False),
    sa.Column('default_unit_g', sa.Float(), nullable=True),
    sa.Column('unit_granularity', sa.String(length=10), nullable=False),
    sa.Column('unit_name', sa.String(length=30), nullable=True),
    sa.Column('portion_step_g', sa.Float(), server_default='10', nullable=False),
    sa.Column('portion_min_g', sa.Float(), nullable=True),
    sa.Column('portion_max_g', sa.Float(), nullable=True),
    sa.Column('meal_slots', sa.JSON(), server_default='[]', nullable=False),
    sa.Column('slot_weights', sa.JSON(), server_default='{}', nullable=False),
    sa.Column('is_free', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('free_text', sa.String(length=60), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_foods_category'), 'foods', ['category'], unique=False)
    op.create_index(op.f('ix_foods_name_norm'), 'foods', ['name_norm'], unique=False)
    op.create_index(op.f('ix_foods_tenant_id'), 'foods', ['tenant_id'], unique=False)
    op.create_table('generation_jobs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('idempotency_key', sa.String(length=120), nullable=False),
    sa.Column('input_hash', sa.String(length=64), nullable=True),
    sa.Column('result_id', sa.Uuid(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'idempotency_key')
    )
    op.create_index(op.f('ix_generation_jobs_status'), 'generation_jobs', ['status'], unique=False)
    op.create_index(op.f('ix_generation_jobs_tenant_id'), 'generation_jobs', ['tenant_id'], unique=False)
    op.create_table('recipes',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('ingredients', sa.JSON(), nullable=False),
    sa.Column('macros', sa.JSON(), nullable=False),
    sa.Column('total_grams', sa.Float(), nullable=False),
    sa.Column('meal_slots', sa.JSON(), server_default='[]', nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('compound_food_id', sa.Uuid(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_recipes_status'), 'recipes', ['status'], unique=False)
    op.create_index(op.f('ix_recipes_tenant_id'), 'recipes', ['tenant_id'], unique=False)
    op.create_table('tenants',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('users',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('provider', sa.String(length=20), nullable=False),
    sa.Column('provider_subject', sa.String(length=255), nullable=True),
    sa.Column('password_hash', sa.String(length=255), nullable=True),
    sa.Column('email_verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('consent_analytics_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('max_menus', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider', 'provider_subject', name='users_provider_identity')
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
    op.create_index(op.f('ix_users_tenant_id'), 'users', ['tenant_id'], unique=False)
    op.create_table('app_feedback',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('category', sa.String(length=20), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('nps', sa.SmallInteger(), nullable=True),
    sa.Column('app_version', sa.String(length=20), nullable=True),
    sa.Column('platform', sa.String(length=20), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_app_feedback_category'), 'app_feedback', ['category'], unique=False)
    op.create_index(op.f('ix_app_feedback_created_at'), 'app_feedback', ['created_at'], unique=False)
    op.create_index(op.f('ix_app_feedback_tenant_id'), 'app_feedback', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_app_feedback_user_id'), 'app_feedback', ['user_id'], unique=False)
    op.create_table('clients',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('sex', sa.String(length=10), nullable=False),
    sa.Column('age_years', sa.Integer(), nullable=False),
    sa.Column('height_cm', sa.Float(), nullable=False),
    sa.Column('weight_kg', sa.Float(), nullable=False),
    sa.Column('goal', sa.String(length=20), nullable=False),
    sa.Column('activity_level', sa.String(length=20), nullable=False),
    sa.Column('city', sa.String(length=120), nullable=True),
    sa.Column('country', sa.String(length=2), nullable=True),
    sa.Column('restrictions', sa.JSON(), nullable=False),
    sa.Column('dislikes', sa.JSON(), server_default='[]', nullable=False),
    sa.Column('context_tags', sa.JSON(), server_default='[]', nullable=False),
    sa.Column('eating_pattern_raw', sa.Text(), nullable=True),
    sa.Column('meal_slots', sa.JSON(), nullable=True),
    sa.Column('free_meal_day', sa.SmallInteger(), nullable=True),
    sa.Column('free_meal_slot', sa.String(length=20), nullable=True),
    sa.Column('active_plan_id', sa.Uuid(), nullable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id')
    )
    op.create_index(op.f('ix_clients_tenant_id'), 'clients', ['tenant_id'], unique=False)
    op.create_table('client_food_bans',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('client_id', sa.Uuid(), nullable=False),
    sa.Column('food_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], ),
    sa.ForeignKeyConstraint(['food_id'], ['foods.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('client_id', 'food_id')
    )
    op.create_index(op.f('ix_client_food_bans_client_id'), 'client_food_bans', ['client_id'], unique=False)
    op.create_index(op.f('ix_client_food_bans_tenant_id'), 'client_food_bans', ['tenant_id'], unique=False)
    op.create_table('client_food_preferences',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('client_id', sa.Uuid(), nullable=False),
    sa.Column('food_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], ),
    sa.ForeignKeyConstraint(['food_id'], ['foods.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('client_id', 'food_id')
    )
    op.create_index(op.f('ix_client_food_preferences_client_id'), 'client_food_preferences', ['client_id'], unique=False)
    op.create_index(op.f('ix_client_food_preferences_tenant_id'), 'client_food_preferences', ['tenant_id'], unique=False)
    op.create_table('nutrition_targets',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('client_id', sa.Uuid(), nullable=False),
    sa.Column('daily', sa.JSON(), nullable=False),
    sa.Column('per_meal', sa.JSON(), nullable=False),
    sa.Column('config_version', sa.String(length=40), nullable=False),
    sa.Column('overrides', sa.JSON(), nullable=False),
    sa.Column('formula', sa.JSON(), nullable=False),
    sa.Column('weight_kg', sa.Float(), nullable=True),
    sa.Column('computed_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_nutrition_targets_client_id'), 'nutrition_targets', ['client_id'], unique=False)
    op.create_index(op.f('ix_nutrition_targets_tenant_id'), 'nutrition_targets', ['tenant_id'], unique=False)
    op.create_table('plan_cycles',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('client_id', sa.Uuid(), nullable=False),
    sa.Column('targets_id', sa.Uuid(), nullable=False),
    sa.Column('variant', sa.Integer(), server_default='0', nullable=False),
    sa.Column('version', sa.SmallInteger(), server_default='1', nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('config_version', sa.String(length=40), nullable=False),
    sa.Column('prompt_version', sa.String(length=60), nullable=False),
    sa.Column('model', sa.String(length=60), nullable=False),
    sa.Column('input_hash', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('edited_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('edit_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('refined_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('refine_model', sa.String(length=60), nullable=True),
    sa.Column('refine_prompt_version', sa.String(length=60), nullable=True),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], ),
    sa.ForeignKeyConstraint(['targets_id'], ['nutrition_targets.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'input_hash', 'variant')
    )
    op.create_index(op.f('ix_plan_cycles_client_id'), 'plan_cycles', ['client_id'], unique=False)
    op.create_index(op.f('ix_plan_cycles_input_hash'), 'plan_cycles', ['input_hash'], unique=False)
    op.create_index(op.f('ix_plan_cycles_status'), 'plan_cycles', ['status'], unique=False)
    op.create_index(op.f('ix_plan_cycles_tenant_id'), 'plan_cycles', ['tenant_id'], unique=False)
    op.create_table('day_plans',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('plan_cycle_id', sa.Uuid(), nullable=False),
    sa.Column('day_index', sa.Integer(), nullable=False),
    sa.Column('totals', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['plan_cycle_id'], ['plan_cycles.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('plan_cycle_id', 'day_index')
    )
    op.create_index(op.f('ix_day_plans_plan_cycle_id'), 'day_plans', ['plan_cycle_id'], unique=False)
    op.create_index(op.f('ix_day_plans_tenant_id'), 'day_plans', ['tenant_id'], unique=False)
    op.create_table('dish_ratings',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('plan_cycle_id', sa.Uuid(), nullable=False),
    sa.Column('day_index', sa.SmallInteger(), nullable=False),
    sa.Column('slot', sa.String(length=20), nullable=False),
    sa.Column('template_id', sa.String(length=60), nullable=True),
    sa.Column('dish_key', sa.String(length=64), nullable=True),
    sa.Column('rating', sa.SmallInteger(), nullable=False),
    sa.Column('would_repeat', sa.Boolean(), nullable=True),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['plan_cycle_id'], ['plan_cycles.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('plan_cycle_id', 'day_index', 'slot', name='dish_ratings_one_per_meal')
    )
    op.create_index(op.f('ix_dish_ratings_created_at'), 'dish_ratings', ['created_at'], unique=False)
    op.create_index(op.f('ix_dish_ratings_dish_key'), 'dish_ratings', ['dish_key'], unique=False)
    op.create_index(op.f('ix_dish_ratings_plan_cycle_id'), 'dish_ratings', ['plan_cycle_id'], unique=False)
    op.create_index(op.f('ix_dish_ratings_template_id'), 'dish_ratings', ['template_id'], unique=False)
    op.create_index(op.f('ix_dish_ratings_tenant_id'), 'dish_ratings', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_dish_ratings_user_id'), 'dish_ratings', ['user_id'], unique=False)
    op.create_table('meal_entries',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('day_plan_id', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('slot', sa.String(length=20), nullable=False),
    sa.Column('template_id', sa.String(length=60), nullable=True),
    sa.Column('dish_name', sa.String(length=160), nullable=True),
    sa.Column('dish_key', sa.String(length=64), nullable=True),
    sa.Column('computed', sa.JSON(), nullable=False),
    sa.Column('free_salad', sa.Boolean(), nullable=False),
    sa.Column('is_free_meal', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('free_protein', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['day_plan_id'], ['day_plans.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_meal_entries_day_plan_id'), 'meal_entries', ['day_plan_id'], unique=False)
    op.create_index(op.f('ix_meal_entries_dish_key'), 'meal_entries', ['dish_key'], unique=False)
    op.create_index(op.f('ix_meal_entries_tenant_id'), 'meal_entries', ['tenant_id'], unique=False)
    op.create_table('meal_items',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('meal_entry_id', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('food_id', sa.Uuid(), nullable=True),
    sa.Column('recipe_id', sa.Uuid(), nullable=True),
    sa.Column('grams', sa.Float(), nullable=True),
    sa.Column('is_free', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('is_locked', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.CheckConstraint('(food_id IS NOT NULL AND recipe_id IS NULL) OR (food_id IS NULL AND recipe_id IS NOT NULL)', name='meal_items_one_source'),
    sa.CheckConstraint('is_free OR (grams IS NOT NULL AND grams > 0)', name='meal_items_grams_positive'),
    sa.ForeignKeyConstraint(['food_id'], ['foods.id'], ),
    sa.ForeignKeyConstraint(['meal_entry_id'], ['meal_entries.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['recipe_id'], ['recipes.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_meal_items_meal_entry_id'), 'meal_items', ['meal_entry_id'], unique=False)
    op.create_index(op.f('ix_meal_items_tenant_id'), 'meal_items', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_meal_items_tenant_id'), table_name='meal_items')
    op.drop_index(op.f('ix_meal_items_meal_entry_id'), table_name='meal_items')
    op.drop_table('meal_items')
    op.drop_index(op.f('ix_meal_entries_tenant_id'), table_name='meal_entries')
    op.drop_index(op.f('ix_meal_entries_dish_key'), table_name='meal_entries')
    op.drop_index(op.f('ix_meal_entries_day_plan_id'), table_name='meal_entries')
    op.drop_table('meal_entries')
    op.drop_index(op.f('ix_dish_ratings_user_id'), table_name='dish_ratings')
    op.drop_index(op.f('ix_dish_ratings_tenant_id'), table_name='dish_ratings')
    op.drop_index(op.f('ix_dish_ratings_template_id'), table_name='dish_ratings')
    op.drop_index(op.f('ix_dish_ratings_plan_cycle_id'), table_name='dish_ratings')
    op.drop_index(op.f('ix_dish_ratings_dish_key'), table_name='dish_ratings')
    op.drop_index(op.f('ix_dish_ratings_created_at'), table_name='dish_ratings')
    op.drop_table('dish_ratings')
    op.drop_index(op.f('ix_day_plans_tenant_id'), table_name='day_plans')
    op.drop_index(op.f('ix_day_plans_plan_cycle_id'), table_name='day_plans')
    op.drop_table('day_plans')
    op.drop_index(op.f('ix_plan_cycles_tenant_id'), table_name='plan_cycles')
    op.drop_index(op.f('ix_plan_cycles_status'), table_name='plan_cycles')
    op.drop_index(op.f('ix_plan_cycles_input_hash'), table_name='plan_cycles')
    op.drop_index(op.f('ix_plan_cycles_client_id'), table_name='plan_cycles')
    op.drop_table('plan_cycles')
    op.drop_index(op.f('ix_nutrition_targets_tenant_id'), table_name='nutrition_targets')
    op.drop_index(op.f('ix_nutrition_targets_client_id'), table_name='nutrition_targets')
    op.drop_table('nutrition_targets')
    op.drop_index(op.f('ix_client_food_preferences_tenant_id'), table_name='client_food_preferences')
    op.drop_index(op.f('ix_client_food_preferences_client_id'), table_name='client_food_preferences')
    op.drop_table('client_food_preferences')
    op.drop_index(op.f('ix_client_food_bans_tenant_id'), table_name='client_food_bans')
    op.drop_index(op.f('ix_client_food_bans_client_id'), table_name='client_food_bans')
    op.drop_table('client_food_bans')
    op.drop_index(op.f('ix_clients_tenant_id'), table_name='clients')
    op.drop_table('clients')
    op.drop_index(op.f('ix_app_feedback_user_id'), table_name='app_feedback')
    op.drop_index(op.f('ix_app_feedback_tenant_id'), table_name='app_feedback')
    op.drop_index(op.f('ix_app_feedback_created_at'), table_name='app_feedback')
    op.drop_index(op.f('ix_app_feedback_category'), table_name='app_feedback')
    op.drop_table('app_feedback')
    op.drop_index(op.f('ix_users_tenant_id'), table_name='users')
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_table('users')
    op.drop_table('tenants')
    op.drop_index(op.f('ix_recipes_tenant_id'), table_name='recipes')
    op.drop_index(op.f('ix_recipes_status'), table_name='recipes')
    op.drop_table('recipes')
    op.drop_index(op.f('ix_generation_jobs_tenant_id'), table_name='generation_jobs')
    op.drop_index(op.f('ix_generation_jobs_status'), table_name='generation_jobs')
    op.drop_table('generation_jobs')
    op.drop_index(op.f('ix_foods_tenant_id'), table_name='foods')
    op.drop_index(op.f('ix_foods_name_norm'), table_name='foods')
    op.drop_index(op.f('ix_foods_category'), table_name='foods')
    op.drop_table('foods')
    op.drop_index(op.f('ix_dish_recipes_template_id'), table_name='dish_recipes')
    op.drop_index(op.f('ix_dish_recipes_dish_key'), table_name='dish_recipes')
    op.drop_table('dish_recipes')
    op.drop_index(op.f('ix_audit_log_tenant_id'), table_name='audit_log')
    op.drop_table('audit_log')
    op.drop_index(op.f('ix_app_events_user_id'), table_name='app_events')
    op.drop_index(op.f('ix_app_events_tenant_id'), table_name='app_events')
    op.drop_index('ix_app_events_name_at', table_name='app_events')
    op.drop_table('app_events')
