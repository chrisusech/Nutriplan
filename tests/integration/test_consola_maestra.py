"""La consola del super_user: ver a todo el mundo y poder intervenir.

Es la única parte de la app que no es de usuario final, y la única que cruza
tenants. Por eso lo primero que se prueba es quién NO puede entrar.
"""

import asyncio
import re

import pytest
from fastapi.testclient import TestClient

from nutriplan.adapters.db.seed import DEFAULT_TENANT_ID
from nutriplan.config.settings import Settings
from nutriplan.container import Container
from nutriplan.ui.web.app import create_app

ADMIN = "admin@nutriplan.test"
CLAVE_ADMIN = "clave-admin-1"


@pytest.fixture
def container(tmp_path, monkeypatch) -> Container:
    monkeypatch.setattr(Settings, "branding_dir", property(lambda _s: tmp_path / "b"))
    return Container(
        settings=Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path}/admin.db",
            admin_email=ADMIN,
            admin_password=CLAVE_ADMIN,
        ),
        tenant_id=DEFAULT_TENANT_ID,
    )


@pytest.fixture
def app(container):
    with TestClient(create_app(container)) as client:
        yield client


def _como_admin(app: TestClient) -> None:
    app.post("/logout", follow_redirects=False)
    resp = app.post(
        "/login", data={"email": ADMIN, "password": CLAVE_ADMIN}, follow_redirects=False
    )
    assert resp.status_code == 303, resp.text


def _como_usuaria(app: TestClient, email: str = "ana@correo.com") -> None:
    app.post("/logout", follow_redirects=False)
    app.post(
        "/registro",
        data={"name": "Ana", "email": email, "password": "clave-segura-1"},
        follow_redirects=False,
    )


def _con_perfil(app: TestClient) -> None:
    app.post(
        "/onboarding",
        data={
            "name": "Ana",
            "sex": "female",
            "age_years": "30",
            "height_cm": "165",
            "weight_kg": "62",
            "goal": "lose_fat",
            "activity_level": "moderate",
            "meal_slots": ["desayuno", "almuerzo", "cena"],
        },
        follow_redirects=False,
    )


def _id_de(app: TestClient, email: str) -> str:
    html = app.get(f"/admin/usuarios?q={email}").text
    filas = html.split('<li class="card admin-row">')[1:]
    for fila in filas:
        if email in fila:
            encontrado = re.search(r"/admin/usuarios/([0-9a-f-]{36})", fila)
            assert encontrado is not None, fila[:400]
            return encontrado.group(1)
    raise AssertionError(f"{email} no aparece en el listado")


# --- Quién entra ------------------------------------------------------------


def test_una_usuaria_normal_no_llega_a_la_consola(app) -> None:
    _como_usuaria(app)
    resp = app.get("/admin/usuarios", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


def test_una_usuaria_normal_tampoco_puede_desactivar_a_nadie(app) -> None:
    """Esconder la pantalla no basta si la ruta sigue abierta."""
    _como_usuaria(app, "otra@correo.com")
    _como_admin(app)
    victima = _id_de(app, "otra@correo.com")

    _como_usuaria(app, "atacante@correo.com")
    resp = app.post(
        f"/admin/usuarios/{victima}/estado", data={"activo": "0"}, follow_redirects=False
    )
    assert resp.headers["location"] == "/"

    _como_admin(app)
    assert "Bloqueada" not in app.get("/admin/usuarios?q=otra@correo.com").text


def test_una_usuaria_normal_no_puede_regalarse_semanas(app) -> None:
    """La palanca del negocio es del dueño, no de quien pasa por la URL."""
    _como_usuaria(app, "ana@correo.com")
    _como_admin(app)
    ana = _id_de(app, "ana@correo.com")

    _como_usuaria(app, "vivo@correo.com")
    resp = app.post(
        f"/admin/usuarios/{ana}/semanas",
        data={"semanas": "52", "dias": "365"},
        follow_redirects=False,
    )
    assert resp.headers["location"] == "/"

    _como_admin(app)
    assert "52 semanas" not in app.get(f"/admin/usuarios/{ana}").text


def test_sin_sesion_no_se_ve_nada(app) -> None:
    app.post("/logout", follow_redirects=False)
    resp = app.get("/admin/usuarios", follow_redirects=False)
    assert resp.status_code == 303


# --- Lo que el super_user puede hacer ---------------------------------------


def test_la_ruta_vieja_de_clientes_manda_a_metricas(app) -> None:
    _como_admin(app)
    resp = app.get("/admin/clientes", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/metricas"


def test_sin_buscar_usuarios_manda_a_metricas(app) -> None:
    _como_admin(app)
    resp = app.get("/admin/usuarios", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/metricas"


def test_el_super_user_encuentra_a_quien_busca(app) -> None:
    _como_usuaria(app, "ana@correo.com")
    _como_admin(app)
    assert "ana@correo.com" in app.get("/admin/usuarios?q=ana@correo.com").text
    assert "ana@correo.com" not in app.get("/admin/metricas").text


def _pestanas(html: str) -> list[str]:
    barra = html.split('<nav class="nav"')[1].split("</nav>")[0]
    return re.findall(r'<a href="([^"]+)"', barra)


def test_el_admin_sin_ficha_entra_a_la_consola_no_al_cuestionario(app) -> None:
    """El login aterriza en la consola; Semana se abre vacía, no rebota."""
    app.post("/logout", follow_redirects=False)
    resp = app.post(
        "/login", data={"email": ADMIN, "password": CLAVE_ADMIN}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/metricas"
    home = app.get("/", follow_redirects=False)
    assert home.status_code == 200
    assert "Aún no tienes un menú" in home.text
    assert 'href="/onboarding"' in home.text
    assert 'id="shell"' in home.text
    assert 'hx-boost="true"' in home.text
    onb = app.get("/onboarding", follow_redirects=False)
    assert onb.status_code == 200
    assert "¿Cómo te llamas?" in onb.text


def test_la_barra_de_abajo_no_se_llena_de_pestanas(app) -> None:
    """Cinco caben en un móvil; ocho se rompen unas encima de otras."""
    _como_admin(app)
    _con_perfil(app)
    pestanas = _pestanas(app.get("/").text)
    assert len(pestanas) <= 5, pestanas
    assert "/admin/usuarios" in pestanas


def test_las_secciones_de_la_consola_estan_dentro_de_la_consola(app) -> None:
    """La barra de abajo es de quien usa la app; la consola lleva la suya."""
    _como_admin(app)
    html = app.get("/admin/recetas").text
    assert "/admin/metricas" not in _pestanas(html)
    for seccion in (
        "/admin/usuarios",
        "/admin/metricas",
        "/admin/laboratorio",
        "/admin/restaurantes",
    ):
        assert f'href="{seccion}"' in html


def test_quien_usa_la_app_no_ve_la_consola_en_su_barra(app) -> None:
    _como_usuaria(app, "ana@correo.com")
    _con_perfil(app)
    assert not any(p.startswith("/admin") for p in _pestanas(app.get("/").text))


def test_puede_crear_una_cuenta_a_mano(app) -> None:
    """Para dar acceso a alguien sin que pase por el alta pública."""
    _como_admin(app)
    resp = app.post(
        "/admin/usuarios",
        data={"name": "Invitado", "email": "invitado@correo.com", "password": "clave-segura-1"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "invitado@correo.com" in app.get("/admin/usuarios?q=invitado@correo.com").text


def test_crear_una_cuenta_con_un_correo_repetido_avisa_y_no_duplica(app, container) -> None:
    _como_usuaria(app, "ana@correo.com")
    _como_admin(app)
    resp = app.post(
        "/admin/usuarios",
        data={"name": "Ana", "email": "ana@correo.com", "password": "clave-segura-1"},
        follow_redirects=False,
    )
    assert "error=" in resp.headers["location"]

    async def _cuantas() -> int:
        async with container.session_factory() as session:
            todas = await container.auth_repo(session).list_accounts()
            return sum(1 for c in todas if c.email == "ana@correo.com")

    assert asyncio.run(_cuantas()) == 1


def _entrar_como_ana(app: TestClient) -> None:
    app.post("/logout", follow_redirects=False)
    entrada = app.post(
        "/login",
        data={"email": "ana@correo.com", "password": "clave-segura-1"},
        follow_redirects=False,
    )
    assert entrada.status_code == 303, "Desactivar una cuenta no es echarla del login"


def test_desactivar_una_cuenta_la_deja_entrar_pero_solo_a_ver_el_aviso(app) -> None:
    """Quien no ha pagado entra y entiende por qué no ve nada, en vez de creer
    que se equivocó de contraseña."""
    _como_usuaria(app, "ana@correo.com")
    _como_admin(app)
    app.post(
        f"/admin/usuarios/{_id_de(app, 'ana@correo.com')}/estado",
        data={"activo": "0"},
        follow_redirects=False,
    )

    _entrar_como_ana(app)
    semana = app.get("/", follow_redirects=False)
    assert semana.status_code == 303
    assert semana.headers["location"] == "/plan-inactivo"
    assert "Tu plan ya no está activo" in app.get("/plan-inactivo").text


def test_una_cuenta_desactivada_tampoco_entra_por_otra_puerta(app) -> None:
    """El aviso no puede ser una pantalla que se esquiva escribiendo la URL."""
    _como_usuaria(app, "ana@correo.com")
    _como_admin(app)
    app.post(
        f"/admin/usuarios/{_id_de(app, 'ana@correo.com')}/estado",
        data={"activo": "0"},
        follow_redirects=False,
    )

    _entrar_como_ana(app)
    for ruta in ("/compra", "/perfil", "/progreso", "/admin/usuarios"):
        resp = app.get(ruta, follow_redirects=False)
        assert resp.headers.get("location") == "/plan-inactivo", ruta


def test_volver_a_activarla_la_devuelve_a_su_semana(app) -> None:
    _como_usuaria(app, "ana@correo.com")
    _como_admin(app)
    ana = _id_de(app, "ana@correo.com")
    app.post(f"/admin/usuarios/{ana}/estado", data={"activo": "0"}, follow_redirects=False)
    app.post(f"/admin/usuarios/{ana}/estado", data={"activo": "1"}, follow_redirects=False)

    _entrar_como_ana(app)
    assert app.get("/plan-inactivo", follow_redirects=False).headers["location"] == "/"


def test_activarle_el_plan_a_alguien_le_da_cuatro_semanas_mas(app) -> None:
    """La operación con la que se cobra: se le concede el mes."""
    _como_usuaria(app, "ana@correo.com")
    _como_admin(app)
    ana = _id_de(app, "ana@correo.com")

    resp = app.post(
        f"/admin/usuarios/{ana}/semanas",
        data={"semanas": "4", "dias": "30", "nota": "Pago de agosto"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    ficha = app.get(f"/admin/usuarios/{ana}").text
    assert "4 semanas" in ficha
    assert "Pago de agosto" in ficha


# Ana pesa 62 kg: 124 g de proteína son 2 g/kg y 56 g de grasa, 0.9 g/kg.
MACROS = {"proteina_g": "124", "carbo_g": "207", "grasa_g": "56"}


def test_puede_ajustarle_los_macros_a_mano(app) -> None:
    """Cuando el cálculo no cuadra con lo que el entrenador ve en la persona."""
    _como_usuaria(app, "ana@correo.com")
    _con_perfil(app)
    _como_admin(app)
    ana = _id_de(app, "ana@correo.com")

    resp = app.post(f"/admin/usuarios/{ana}/macros", data=MACROS, follow_redirects=False)
    assert resp.status_code == 303

    ficha = app.get(f"/admin/usuarios/{ana}").text
    assert "124 g proteína" in ficha
    assert "207 g carbos" in ficha
    assert "56 g grasa" in ficha


def test_el_carbohidrato_tambien_se_ajusta_y_las_kcal_lo_acompanan(app) -> None:
    """Las kcal son 4·P + 4·C + 9·G: subir el carbo tiene que subirlas."""
    _como_usuaria(app, "ana@correo.com")
    _con_perfil(app)
    _como_admin(app)
    ana = _id_de(app, "ana@correo.com")

    app.post(
        f"/admin/usuarios/{ana}/macros",
        data={**MACROS, "carbo_g": "250"},
        follow_redirects=False,
    )
    ficha = app.get(f"/admin/usuarios/{ana}").text
    assert "250 g carbos" in ficha
    assert "2000 kcal" in ficha  # 496 + 1000 + 504


def test_lo_que_ajusto_a_mano_puede_quedar_bajo_llave(app) -> None:
    """Si no, el check-in de la semana siguiente deshace el ajuste."""
    _como_usuaria(app, "ana@correo.com")
    _con_perfil(app)
    _como_admin(app)
    ana = _id_de(app, "ana@correo.com")

    app.post(
        f"/admin/usuarios/{ana}/macros",
        data={**MACROS, "bloquear": "1"},
        follow_redirects=False,
    )
    assert "Bloqueado a mano" in app.get(f"/admin/usuarios/{ana}").text


def test_unos_macros_absurdos_se_explican_en_vez_de_guardarse(app) -> None:
    _como_usuaria(app, "ana@correo.com")
    _con_perfil(app)
    _como_admin(app)
    ana = _id_de(app, "ana@correo.com")

    resp = app.post(
        f"/admin/usuarios/{ana}/macros",
        data={**MACROS, "carbo_g": "2000"},
        follow_redirects=False,
    )
    assert "rango permitido" in app.get(resp.headers["location"]).text


def test_un_deficit_bajo_el_metabolismo_basal_se_rechaza_con_su_motivo(app) -> None:
    """La guarda del dominio tiene que llegar a la pantalla, no ser un 500."""
    _como_usuaria(app, "ana@correo.com")
    _con_perfil(app)
    _como_admin(app)
    ana = _id_de(app, "ana@correo.com")

    resp = app.post(
        f"/admin/usuarios/{ana}/macros",
        data={"proteina_g": "100", "carbo_g": "100", "grasa_g": "40"},
        follow_redirects=False,
    )
    assert "piso" in app.get(resp.headers["location"]).text


def test_no_se_pueden_ajustar_macros_de_quien_no_tiene_perfil(app) -> None:
    _como_usuaria(app, "ana@correo.com")
    _como_admin(app)
    ana = _id_de(app, "ana@correo.com")

    resp = app.post(f"/admin/usuarios/{ana}/macros", data=MACROS, follow_redirects=False)
    assert "error=" in resp.headers["location"]
    assert "aún no completó su perfil" in app.get(f"/admin/usuarios/{ana}").text


def test_la_ficha_de_quien_no_existe_devuelve_404_y_no_una_traza(app) -> None:
    _como_admin(app)
    resp = app.get("/admin/usuarios/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


def test_un_id_que_no_es_un_id_no_tumba_la_consola(app) -> None:
    _como_admin(app)
    resp = app.post(
        "/admin/usuarios/no-soy-un-uuid/estado", data={"activo": "0"}, follow_redirects=False
    )
    assert resp.status_code in (303, 400)


def test_el_laboratorio_del_super_user_esta_disponible(app) -> None:
    """Su pestaña para probar el producto sin ensuciar las métricas."""
    _como_admin(app)
    resp = app.get("/admin/laboratorio")
    assert resp.status_code == 200
    assert "Laboratorio" in resp.text


def test_una_usuaria_normal_no_llega_a_restaurantes(app) -> None:
    _como_usuaria(app)
    resp = app.get("/admin/restaurantes", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


def test_el_admin_anade_un_restaurante_y_un_plato(tmp_path, monkeypatch) -> None:
    """Escribe el YAML y recarga: duplicar avisa, no revienta."""
    from urllib.parse import unquote

    catalogo = tmp_path / "restaurants.yaml"
    catalogo.write_text('version: "1.0.0"\nrestaurants: []\n', encoding="utf-8")
    monkeypatch.setattr(Settings, "branding_dir", property(lambda _s: tmp_path / "b"))
    monkeypatch.setattr(Settings, "restaurants_catalog_path", property(lambda _s: catalogo))
    container = Container(
        settings=Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path}/rest.db",
            admin_email=ADMIN,
            admin_password=CLAVE_ADMIN,
        ),
        tenant_id=DEFAULT_TENANT_ID,
    )
    with TestClient(create_app(container)) as app:
        _como_admin(app)
        html = app.get("/admin/restaurantes").text
        assert "Restaurantes" in html

        resp = app.post(
            "/admin/restaurantes",
            data={"id": "Demo", "name": "Demo Café"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "ok=" in resp.headers["location"]
        assert "Demo Café" in app.get("/admin/restaurantes").text

        otra = app.post(
            "/admin/restaurantes",
            data={"id": "demo", "name": "Otro"},
            follow_redirects=False,
        )
        assert "ya estaba" in unquote(otra.headers["location"])

        plato = app.post(
            "/admin/restaurantes/demo/platos",
            data={
                "id": "bowl",
                "name": "Bowl de pollo",
                "portion": "1 plato",
                "protein_g": "35",
                "carb_g": "40",
                "fat_g": "12",
            },
            follow_redirects=False,
        )
        assert plato.status_code == 303
        pagina = app.get("/admin/restaurantes").text
        assert "Bowl de pollo" in pagina
        assert "408 kcal" in pagina

        dup = app.post(
            "/admin/restaurantes/demo/platos",
            data={
                "id": "bowl",
                "name": "Otro bowl",
                "protein_g": "10",
                "carb_g": "10",
                "fat_g": "10",
            },
            follow_redirects=False,
        )
        assert "ya estaba" in unquote(dup.headers["location"])

        vacio = app.post(
            "/admin/restaurantes",
            data={"id": "   ", "name": "Sin id"},
            follow_redirects=False,
        )
        assert vacio.status_code == 303
        assert "error=" in vacio.headers["location"]

        fantasma = app.post(
            "/admin/restaurantes/no-existe/platos",
            data={"id": "x", "name": "X", "protein_g": "1", "carb_g": "1", "fat_g": "1"},
            follow_redirects=False,
        )
        assert fantasma.status_code == 303
        assert "error=" in fantasma.headers["location"]
        assert "No está" in unquote(fantasma.headers["location"])
