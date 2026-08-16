"""Nombre de cocina en español a partir de la descripción de USDA. Sin IA.

USDA escribe códigos de inventario, no nombres: «Beef, chuck, arm pot roast,
separable lean only, trimmed to 1/8" fat, choice, cooked, braised». Casi todo eso
es contabilidad de despiece y no distingue dos alimentos en una cocina. Lo que
queda —el animal y el corte— sí.

Por eso la traducción es un léxico y no un modelo: el vocabulario de USDA es
controlado y pequeño (641 cabezas para 5.299 filas), así que un diccionario lo
cubre entero, corre en un segundo, cuesta cero y da el mismo resultado siempre.
Una fila cuya cabeza no esté en el léxico devuelve None y se va a cuarentena:
nadie adivina un nombre.

El efecto secundario es el que se busca: 859 filas de «beef» colapsan en los
pocos cortes que alguien cocina de verdad, en vez de en 859 casi duplicados.
"""

from __future__ import annotations

import re

# Cortes de carne. La clave es (animal, corte) porque en español el corte manda:
# no se dice «carne de pollo, pechuga» sino «pechuga de pollo».
_CORTES: dict[tuple[str, str], str] = {
    ("beef", "loin"): "lomo de res",
    ("beef", "top loin steak"): "lomo de res",
    ("beef", "tenderloin"): "lomo fino de res",
    ("beef", "short loin"): "lomo de res",
    ("beef", "round"): "posta de res",
    ("beef", "top round"): "posta de res",
    ("beef", "bottom round"): "muchacho de res",
    ("beef", "eye of round"): "muchacho redondo de res",
    ("beef", "chuck"): "paleta de res",
    ("beef", "shoulder"): "paleta de res",
    ("beef", "brisket"): "pecho de res",
    ("beef", "rib"): "costilla de res",
    ("beef", "rib eye steak"): "ojo de bife",
    ("beef", "flank"): "sobrebarriga",
    ("beef", "skirt steak"): "arrachera",
    ("beef", "plate steak"): "sobrebarriga",
    ("beef", "shank"): "osobuco de res",
    ("beef", "ground"): "carne molida de res",
    ("beef", "liver"): "hígado de res",
    ("beef", "tongue"): "lengua de res",
    ("beef", "oxtail"): "rabo de res",
    ("pork", "loin"): "lomo de cerdo",
    ("pork", "tenderloin"): "solomillo de cerdo",
    ("pork", "shoulder"): "paleta de cerdo",
    ("pork", "leg"): "pernil de cerdo",
    ("pork", "ham"): "pernil de cerdo",
    ("pork", "rib"): "costilla de cerdo",
    ("pork", "spareribs"): "costilla de cerdo",
    ("pork", "belly"): "tocino de cerdo",
    ("pork", "ground"): "carne molida de cerdo",
    ("pork", "liver"): "hígado de cerdo",
    ("chicken", "breast"): "pechuga de pollo",
    ("chicken", "thigh"): "muslo de pollo",
    ("chicken", "drumstick"): "pierna de pollo",
    ("chicken", "leg"): "pierna de pollo",
    ("chicken", "wing"): "alita de pollo",
    ("chicken", "ground"): "pollo molido",
    ("chicken", "liver"): "hígado de pollo",
    ("turkey", "breast"): "pechuga de pavo",
    ("turkey", "thigh"): "muslo de pavo",
    ("turkey", "drumstick"): "pierna de pavo",
    ("turkey", "leg"): "pierna de pavo",
    ("turkey", "wing"): "alita de pavo",
    ("turkey", "ground"): "pavo molido",
    ("lamb", "loin"): "lomo de cordero",
    ("lamb", "leg"): "pierna de cordero",
    ("lamb", "shoulder"): "paleta de cordero",
    ("lamb", "rib"): "costilla de cordero",
    ("lamb", "ground"): "carne molida de cordero",
    ("veal", "loin"): "lomo de ternera",
    ("veal", "leg"): "pierna de ternera",
    ("veal", "shoulder"): "paleta de ternera",
    ("veal", "rib"): "costilla de ternera",
    ("veal", "ground"): "carne molida de ternera",
}

# Cabeza de la descripción (lo que va antes de la primera coma) → español.
# Una cabeza que no esté aquí manda la fila a cuarentena.
_CABEZAS: dict[str, str] = {
    # Carnes y aves
    "beef": "carne de res",
    "pork": "carne de cerdo",
    "chicken": "pollo",
    "turkey": "pavo",
    "lamb": "cordero",
    "veal": "ternera",
    "game meat": "carne de caza",
    "bison": "bisonte",
    "duck": "pato",
    "goose": "ganso",
    "quail": "codorniz",
    "pheasant": "faisán",
    "ostrich": "avestruz",
    "emu": "emú",
    "squab": "pichón",
    "guinea hen": "gallina de guinea",
    "poultry": "ave",
    "chicken breast": "pechuga de pollo",
    "chicken breast tenders": "tiras de pechuga de pollo",
    "chicken patty": "hamburguesa de pollo",
    "turkey breast": "pechuga de pavo",
    "turkey from whole": "pavo",
    "pork loin": "lomo de cerdo",
    # Embutidos y curados
    "sausage": "salchicha",
    "pork sausage": "salchicha de cerdo",
    "bratwurst": "bratwurst",
    "kielbasa": "salchicha kielbasa",
    "beerwurst": "salchicha beerwurst",
    "frankfurter": "salchicha tipo viena",
    "bologna": "mortadela",
    "salami": "salami",
    "ham": "jamón",
    "bacon": "tocineta",
    "canadian bacon": "tocineta canadiense",
    "pate": "paté",
    "meatballs": "albóndigas",
    # Pescados y mariscos
    "fish": "pescado",
    "fish oil": "aceite de pescado",
    "mollusks": "molusco",
    "crustaceans": "marisco",
    "seaweed": "alga marina",
    # Huevo y lácteos
    "egg": "huevo",
    "milk": "leche",
    "soy milk": "leche de soya",
    "soymilk": "leche de soya",
    "soymilk (all flavors)": "leche de soya",
    "cheese": "queso",
    "cheese food": "queso procesado",
    "cheese spread": "queso untable",
    "cheese product": "queso procesado",
    "yogurt": "yogur",
    "cream": "crema de leche",
    "sour cream": "crema agria",
    "cream substitute": "sustituto de crema",
    "whey": "suero de leche",
    "butter": "mantequilla",
    "ice cream": "helado",
    "ice cream bar": "helado en barra",
    "ice cream sandwich": "sándwich de helado",
    "milk shakes": "malteada",
    "dessert topping": "cobertura de postre",
    # Legumbres
    "beans": "frijol",
    "refried beans": "frijol refrito",
    "lima beans": "frijol lima",
    "mung beans": "frijol mungo",
    "mungo beans": "frijol mungo",
    "mothbeans": "frijol moth",
    "winged beans": "frijol alado",
    "yardlong bean": "frijol largo",
    "yardlong beans": "frijol largo",
    "hyacinth-beans": "frijol jacinto",
    "hyacinth beans": "frijol jacinto",
    "broadbeans": "haba",
    "broadbeans (fava beans)": "haba",
    "cowpeas": "frijol caupí",
    "cowpeas (blackeyes)": "frijol caupí",
    "chickpeas (garbanzo beans": "garbanzo",
    "lentils": "lenteja",
    "peas": "arveja",
    "pigeonpeas": "guandú",
    "pigeon peas (red gram)": "guandú",
    "lupins": "altramuz",
    "soybeans": "soya",
    "edamame": "edamame",
    "tofu": "tofu",
    "hummus": "hummus",
    "soy flour": "harina de soya",
    "soy protein concentrate": "proteína de soya",
    "peanuts": "maní",
    "peanut butter": "mantequilla de maní",
    "peanut flour": "harina de maní",
    # Frutos secos y semillas
    "nuts": "fruto seco",
    "seeds": "semilla",
    # Cereales y derivados
    "rice": "arroz",
    "wild rice": "arroz salvaje",
    "rice flour": "harina de arroz",
    "rice noodles": "fideo de arroz",
    "corn": "maíz",
    "corn grain": "maíz en grano",
    "corn flour": "harina de maíz",
    "cornmeal": "harina de maíz",
    "hominy": "maíz pilado",
    "wheat": "trigo",
    "wheat flour": "harina de trigo",
    "flour": "harina",
    "rye flour": "harina de centeno",
    "sorghum flour": "harina de sorgo",
    "sorghum grain": "sorgo",
    "semolina": "sémola",
    "oat bran": "salvado de avena",
    "barley": "cebada",
    "bulgur": "bulgur",
    "buckwheat": "trigo sarraceno",
    "buckwheat groats": "trigo sarraceno",
    "millet": "mijo",
    "quinoa": "quinoa",
    "amaranth grain": "amaranto",
    "teff": "teff",
    "spelt": "espelta",
    "couscous": "cuscús",
    "pasta": "pasta",
    "macaroni": "macarrón",
    "spaghetti": "espagueti",
    "noodles": "fideo",
    "cereals": "cereal",
    "cereals ready-to-eat": "cereal de caja",
    # Panadería
    "bread": "pan",
    "rolls": "panecillo",
    "sweet rolls": "pan dulce",
    "bagels": "bagel",
    "english muffins": "muffin inglés",
    "muffins": "muffin",
    "tortillas": "tortilla",
    "biscuits": "bizcocho",
    "crackers": "galleta salada",
    "cookies": "galleta",
    "cookie": "galleta",
    "cake": "torta",
    "pie": "pastel",
    "pie crust": "masa de pastel",
    "croissants": "croissant",
    "danish pastry": "pastel danés",
    "doughnuts": "dona",
    "pancakes": "panqueque",
    "waffles": "waffle",
    "waffle": "waffle",
    "toaster pastries": "pastelito de tostadora",
    "leavening agents": "polvo de hornear",
    # Verduras
    "potatoes": "papa",
    "potato puffs": "bolita de papa",
    "sweet potato": "batata",
    "sweet potatoes": "batata",
    "sweet potato leaves": "hoja de batata",
    "yam": "ñame",
    "mountain yam": "ñame de montaña",
    "taro": "malanga",
    "taro leaves": "hoja de malanga",
    "taro shoots": "brote de malanga",
    "tomatoes": "tomate",
    "tomato": "tomate",
    "tomato products": "tomate procesado",
    "onions": "cebolla",
    "shallots": "chalota",
    "leeks": "puerro",
    "chives": "cebollín",
    "garlic": "ajo",
    "ginger root": "jengibre",
    "peppers": "pimentón",
    "carrots": "zanahoria",
    "broccoli": "brócoli",
    "broccoli raab": "brócoli rabe",
    "cauliflower": "coliflor",
    "cabbage": "repollo",
    "brussels sprouts": "repollito de bruselas",
    "kale": "kale",
    "spinach": "espinaca",
    "mustard spinach": "espinaca mostaza",
    "new zealand spinach": "espinaca de nueva zelanda",
    "lettuce": "lechuga",
    "radicchio": "radicchio",
    "arugula": "rúgula",
    "chard": "acelga",
    "collards": "berza",
    "turnip greens": "hoja de nabo",
    "turnip greens and turnips": "hoja de nabo",
    "turnips": "nabo",
    "mustard greens": "hoja de mostaza",
    "beet greens": "hoja de remolacha",
    "dandelion greens": "diente de león",
    "amaranth leaves": "hoja de amaranto",
    "pumpkin leaves": "hoja de auyama",
    "asparagus": "espárrago",
    "celery": "apio",
    "celeriac": "apionabo",
    "cucumber": "pepino",
    "eggplant": "berenjena",
    "squash": "calabaza",
    "pumpkin": "auyama",
    "pumpkin flowers": "flor de auyama",
    "waxgourd": "calabaza cerosa",
    "gourd": "calabaza",
    "chayote": "chayote",
    "okra": "okra",
    "mushrooms": "champiñón",
    "mushroom": "champiñón",
    "beets": "remolacha",
    "radishes": "rábano",
    "parsnips": "chirivía",
    "rutabagas": "colinabo",
    "kohlrabi": "colirrábano",
    "artichokes": "alcachofa",
    "bamboo shoots": "brote de bambú",
    "hearts of palm": "palmito",
    "nopales": "nopal",
    "olives": "aceituna",
    "pickles": "pepinillo",
    "pickle relish": "relish de pepinillo",
    "corn (sweet)": "maíz dulce",
    "peas and carrots": "arveja con zanahoria",
    "peas and onions": "arveja con cebolla",
    "succotash": "succotash",
    "vegetables": "verdura mixta",
    "fennel": "hinojo",
    "parsley": "perejil",
    "cress": "berro",
    "watercress": "berro",
    "purslane": "verdolaga",
    "lambsquarters": "quinua silvestre",
    "salsify": "salsifí",
    "burdock root": "raíz de bardana",
    "lotus root": "raíz de loto",
    "waterchestnuts": "castaña de agua",
    "jicama": "jícama",
    "yambean (jicama)": "jícama",
    "grape leaves": "hoja de parra",
    "fiddlehead ferns": "helecho",
    "plantains": "plátano",
    # Frutas
    "apples": "manzana",
    "applesauce": "compota de manzana",
    "pears": "pera",
    "peaches": "durazno",
    "apricots": "albaricoque",
    "plums": "ciruela",
    "prunes": "ciruela pasa",
    "cherries": "cereza",
    "grapes": "uva",
    "raisins": "uva pasa",
    "currants": "grosella",
    "strawberries": "fresa",
    "raspberries": "frambuesa",
    "blackberries": "mora",
    "blueberries": "arándano",
    "boysenberries": "boysenberry",
    "oranges": "naranja",
    "tangerines": "mandarina",
    "grapefruit": "toronja",
    "bananas": "banano",
    "pineapple": "piña",
    "mango": "mango",
    "papaya": "papaya",
    "guava": "guayaba",
    "melons": "melón",
    "watermelon": "sandía",
    "kiwifruit": "kiwi",
    "avocados": "aguacate",
    "figs": "higo",
    "dates": "dátil",
    "persimmons": "caqui",
    "jackfruit": "jaca",
    "litchis": "lichi",
    "longans": "longan",
    "jujube": "azufaifa",
    "nectarines": "nectarina",
    "rhubarb": "ruibarbo",
    "nance": "nance",
    "fruit cocktail": "cóctel de frutas",
    "fruit salad": "ensalada de frutas",
    # Jugos
    "orange juice": "jugo de naranja",
    "apple juice": "jugo de manzana",
    "grape juice": "jugo de uva",
    "grapefruit juice": "jugo de toronja",
    "pineapple juice": "jugo de piña",
    "cranberry juice": "jugo de arándano",
    "lime juice": "jugo de limón",
    "tomato juice": "jugo de tomate",
    "passion-fruit juice": "jugo de maracuyá",
    "vegetable juice cocktail": "jugo de verduras",
    "guava nectar": "néctar de guayaba",
    "peach nectar": "néctar de durazno",
    "pear nectar": "néctar de pera",
    # Grasas y salsas
    "oil": "aceite",
    "fat": "grasa",
    "shortening": "manteca vegetal",
    "shortening frying (heavy duty)": "manteca vegetal",
    "margarine": "margarina",
    "margarine-like": "margarina",
    "margarine-like spread with yogurt": "margarina con yogur",
    "mayonnaise": "mayonesa",
    "salad dressing": "aderezo",
    "creamy dressing": "aderezo cremoso",
    "sandwich spread": "untable para sándwich",
    "mustard": "mostaza",
    "catsup": "salsa de tomate",
    "vinegar": "vinagre",
    # Condimentos
    "spices": "especia",
    "seasoning mix": "mezcla de especias",
    "spearmint": "hierbabuena",
    "vanilla extract": "extracto de vainilla",
}

# Cabezas genéricas cuya identidad la lleva el SEGUNDO segmento: «Fish, salmon»
# no es pescado, es salmón. Sin esto, doscientas filas de pescados distintos
# acabarían compartiendo el nombre «pescado» y colapsando en una sola.
_ESPECIES: dict[tuple[str, str], str] = {
    ("fish", "salmon"): "salmón",
    ("fish", "tuna"): "atún",
    ("fish", "cod"): "bacalao",
    ("fish", "tilapia"): "tilapia",
    ("fish", "trout"): "trucha",
    ("fish", "sardine"): "sardina",
    ("fish", "mackerel"): "caballa",
    ("fish", "halibut"): "fletán",
    ("fish", "snapper"): "pargo",
    ("fish", "bass"): "róbalo",
    ("fish", "sea bass"): "róbalo",
    ("fish", "sole"): "lenguado",
    ("fish", "flatfish"): "lenguado",
    ("fish", "haddock"): "eglefino",
    ("fish", "anchovy"): "anchoa",
    ("fish", "herring"): "arenque",
    ("fish", "catfish"): "bagre",
    ("fish", "swordfish"): "pez espada",
    ("fish", "grouper"): "mero",
    ("fish", "pollock"): "abadejo",
    ("fish", "carp"): "carpa",
    ("fish", "perch"): "perca",
    ("fish", "mahimahi"): "dorado",
    ("fish", "whiting"): "merluza",
    ("mollusks", "oyster"): "ostra",
    ("mollusks", "clam"): "almeja",
    ("mollusks", "mussel"): "mejillón",
    ("mollusks", "scallop"): "vieira",
    ("mollusks", "squid"): "calamar",
    ("mollusks", "octopus"): "pulpo",
    ("mollusks", "snail"): "caracol",
    ("mollusks", "abalone"): "abulón",
    ("mollusks", "whelk"): "caracol de mar",
    ("mollusks", "cuttlefish"): "sepia",
    ("crustaceans", "shrimp"): "camarón",
    ("crustaceans", "crab"): "cangrejo",
    ("crustaceans", "lobster"): "langosta",
    ("crustaceans", "crayfish"): "langostino",
    ("crustaceans", "spiny lobster"): "langosta",
    ("nuts", "almonds"): "almendra",
    ("nuts", "almond butter"): "mantequilla de almendras",
    ("nuts", "walnuts"): "nuez",
    ("nuts", "cashew nuts"): "marañón",
    ("nuts", "cashew butter"): "mantequilla de marañón",
    ("nuts", "pecans"): "pecana",
    ("nuts", "pistachio nuts"): "pistacho",
    ("nuts", "hazelnuts or filberts"): "avellana",
    ("nuts", "macadamia nuts"): "macadamia",
    ("nuts", "brazilnuts"): "nuez de brasil",
    ("nuts", "pine nuts"): "piñón",
    ("nuts", "chestnuts"): "castaña",
    ("nuts", "coconut meat"): "coco",
    ("nuts", "coconut milk"): "leche de coco",
    ("nuts", "coconut water"): "agua de coco",
    ("nuts", "mixed nuts"): "mezcla de frutos secos",
    ("seeds", "sesame seeds"): "ajonjolí",
    ("seeds", "sesame butter"): "tahini",
    ("seeds", "sunflower seed kernels"): "semilla de girasol",
    ("seeds", "pumpkin and squash seed kernels"): "semilla de auyama",
    ("seeds", "chia seeds"): "chía",
    ("seeds", "flaxseed"): "linaza",
    ("seeds", "hemp seed"): "semilla de cáñamo",
    ("seeds", "poppy seed"): "amapola",
    ("cheese", "cheddar"): "queso cheddar",
    ("cheese", "mozzarella"): "queso mozzarella",
    ("cheese", "parmesan"): "queso parmesano",
    ("cheese", "ricotta"): "queso ricotta",
    ("cheese", "cottage"): "queso cottage",
    ("cheese", "feta"): "queso feta",
    ("cheese", "gouda"): "queso gouda",
    ("cheese", "swiss"): "queso suizo",
    ("cheese", "blue"): "queso azul",
    ("cheese", "cream"): "queso crema",
    ("cheese", "goat"): "queso de cabra",
    ("cheese", "provolone"): "queso provolone",
    ("cheese", "brie"): "queso brie",
    ("cheese", "monterey"): "queso monterey",
    ("cheese", "muenster"): "queso muenster",
    ("cheese", "queso blanco"): "queso blanco",
    ("cheese", "queso fresco"): "queso fresco",
    ("beans", "black"): "frijol negro",
    ("beans", "kidney"): "frijol rojo",
    ("beans", "pinto"): "frijol pinto",
    ("beans", "navy"): "frijol blanco",
    ("beans", "white"): "frijol blanco",
    ("beans", "great northern"): "frijol blanco",
    ("beans", "cranberry (roman)"): "frijol cargamanto",
    ("beans", "snap"): "habichuela",
    ("beans", "adzuki"): "frijol azuki",
    ("beans", "fava"): "haba",
    ("squash", "zucchini"): "calabacín",
    ("squash", "summer"): "calabacín",
    ("squash", "butternut"): "auyama butternut",
    ("squash", "acorn"): "calabaza bellota",
    ("squash", "spaghetti"): "calabaza espagueti",
    ("squash", "hubbard"): "calabaza hubbard",
    ("egg", "white"): "clara de huevo",
    ("egg", "yolk"): "yema de huevo",
    ("egg", "whole"): "huevo entero",
    ("oil", "olive"): "aceite de oliva",
    ("oil", "canola"): "aceite de canola",
    ("oil", "sunflower"): "aceite de girasol",
    ("oil", "coconut"): "aceite de coco",
    ("oil", "corn"): "aceite de maíz",
    ("oil", "soybean"): "aceite de soya",
    ("oil", "avocado"): "aceite de aguacate",
    ("oil", "sesame"): "aceite de ajonjolí",
    ("oil", "peanut"): "aceite de maní",
    ("oil", "palm"): "aceite de palma",
    ("oil", "safflower"): "aceite de cártamo",
    ("oil", "grapeseed"): "aceite de uva",
    ("milk", "goat"): "leche de cabra",
    ("milk", "buttermilk"): "suero de leche",
    ("peppers", "sweet"): "pimentón",
    ("peppers", "hot chili"): "ají picante",
    ("peppers", "jalapeno"): "jalapeño",
    ("peppers", "serrano"): "ají serrano",
    ("peppers", "poblano"): "chile poblano",
    ("rice", "white"): "arroz blanco",
    ("rice", "brown"): "arroz integral",
    ("game meat", "rabbit"): "conejo",
    ("game meat", "deer"): "venado",
    ("game meat", "goat"): "cabra",
    ("game meat", "boar"): "jabalí",
    ("game meat", "buffalo"): "búfalo",
}

# Calificadores que sí distinguen un alimento en la cocina; el resto se tira.
# Los que concuerdan llevan (masculino, femenino): «lechuga rojo» no es español.
_CALIFICADORES: dict[str, tuple[str, str]] = {
    "white": ("blanco", "blanca"),
    "brown": ("integral", "integral"),
    "whole wheat": ("integral", "integral"),
    "whole-wheat": ("integral", "integral"),
    "red": ("rojo", "roja"),
    "green": ("verde", "verde"),
    "yellow": ("amarillo", "amarilla"),
    "black": ("negro", "negra"),
    "sweet": ("dulce", "dulce"),
    "bitter": ("amargo", "amarga"),
    "canned": ("en lata", "en lata"),
    "dried": ("seco", "seca"),
    "dry": ("seco", "seca"),
    "smoked": ("ahumado", "ahumada"),
    "cured": ("curado", "curada"),
    "frozen": ("congelado", "congelada"),
    "sprouted": ("germinado", "germinada"),
    "toasted": ("tostado", "tostada"),
    "reduced fat": ("semidescremado", "semidescremada"),
    "low fat": ("bajo en grasa", "baja en grasa"),
    "lowfat": ("bajo en grasa", "baja en grasa"),
    "nonfat": ("descremado", "descremada"),
    "fat free": ("descremado", "descremada"),
    "skim": ("descremado", "descremada"),
    "whole": ("entero", "entera"),
    "boneless": ("sin hueso", "sin hueso"),
    "skinless": ("sin piel", "sin piel"),
    "lean": ("magro", "magra"),
    "unsweetened": ("sin azúcar", "sin azúcar"),
    "no salt added": ("sin sal", "sin sal"),
    "low sodium": ("bajo en sodio", "baja en sodio"),
    "in oil": ("en aceite", "en aceite"),
    "in water": ("en agua", "en agua"),
}

# Sustantivos que no siguen la regla de la «-a» final.
_FEMENINOS = frozenset({"leche", "carne", "sal", "miel", "col", "flor", "piel"})
_MASCULINOS = frozenset({"día", "aguacate", "tomate", "pate", "puerro"})

# Cabezas que por sí solas no nombran un alimento: son la familia, no el
# ingrediente. «Beef» cubre desde el solomillo hasta el sebo; «cheese» desde el
# cottage hasta el parmesano. Si no se reconoce el corte o la especie, la fila no
# se nombra — un nombre genérico con los macros de una fila cualquiera es peor
# que no tener la fila.
_EXIGEN_DETALLE = frozenset(
    {
        "beef",
        "pork",
        "lamb",
        "veal",
        "game meat",
        "chicken",
        "turkey",
        "poultry",
        "fish",
        "mollusks",
        "crustaceans",
        "nuts",
        "seeds",
        "cheese",
        "beans",
        "squash",
        "peppers",
        "spices",
        "vegetables",
        "sausage",
        "oil",
        "flour",
        "cereals",
        "fat",
    }
)

# Lo que USDA escribe para su despiece y su control de calidad. No dice nada de
# un alimento: son grados comerciales, márgenes de grasa recortada y códigos de
# corte. Se tira antes de mirar nada más.
_RUIDO = re.compile(
    r"^(separable\s|trimmed to|composite of|all grades|choice|select|prime|"
    r"lip[- ]?(on|off)|small end|large end|whole grade|imported|australian|"
    r"new zealand|variety meats|mixed species|dry heat|moist heat|"
    r"with added solution|industrial|commercially prepared|home[- ]prepared|"
    r"prepared with|includes|ns as to|nfs|unprepared|dry mix|"
    r"cooked|boiled|braised|roasted|grilled|broiled|baked|fried|steamed|stewed|"
    r"drained|solids|without salt|with salt|regular pack|"
    r"\d|grade\b|meat only|meat and skin|meat and fat|bone[- ]in)",
    re.IGNORECASE,
)

_ALIASES: dict[str, tuple[str, ...]] = {
    "aguacate": ("palta",),
    "banano": ("plátano", "guineo", "cambur"),
    "frijol": ("fríjol", "poroto", "caraota", "habichuela"),
    "arveja": ("guisante", "chícharo"),
    "batata": ("boniato", "camote"),
    "maní": ("cacahuate",),
    "durazno": ("melocotón",),
    "toronja": ("pomelo",),
    "papa": ("patata",),
    "aguacate hass": ("palta",),
    "pimentón": ("pimiento", "morrón", "ají dulce"),
    "auyama": ("zapallo", "calabaza"),
    "champiñón": ("hongo", "seta"),
    "remolacha": ("betabel",),
    "tocineta": ("tocino", "panceta"),
    "carne molida de res": ("carne picada",),
    "jugo de maracuyá": ("jugo de parchita",),
    "fresa": ("frutilla",),
    "aguacate criollo": ("palta",),
    "malanga": ("taro",),
    "arroz": ("arroz blanco",),
}


def _segmentos(description: str) -> list[str]:
    return [s.strip().lower() for s in description.split(",") if s.strip()]


def _util(segmento: str) -> bool:
    return not _RUIDO.match(segmento)


def nombre_es(description: str) -> str | None:
    """El nombre de cocina, o None si la cabeza no está en el léxico.

    Devolver None es deliberado: una fila sin traducción va a cuarentena y la
    mira una persona. Un catálogo con «Beef, chuck, arm pot roast» dentro es
    peor que un catálogo más corto.
    """
    segmentos = _segmentos(description)
    if not segmentos:
        return None
    cabeza = segmentos[0]
    resto = [s for s in segmentos[1:] if _util(s)]

    # La especie y el corte mandan sobre la cabeza genérica: «salmón», no
    # «pescado»; «pechuga de pollo», no «pollo, pechuga». Gana el segmento más
    # específico, que en USDA es el más largo: «tenderloin» antes que «loin».
    for tabla in (_ESPECIES, _CORTES):
        hallados = [(s, tabla[(cabeza, s)]) for s in resto if (cabeza, s) in tabla]
        if hallados:
            segmento, preciso = max(hallados, key=lambda par: len(par[0]))
            return _componer(preciso, [s for s in resto if s != segmento])

    if cabeza in _EXIGEN_DETALLE:
        # «Beef» a secas no es un alimento: las 150 filas que quedarían bajo
        # «carne de res» van de 128 a 731 kcal, y el motor serviría cualquiera.
        # Sin corte ni especie reconocidos no hay nombre — a cuarentena.
        return None

    base = _CABEZAS.get(cabeza)
    if base is None:
        return None
    return _componer(base, resto)


def _es_femenino(base: str) -> bool:
    """Género del sustantivo que encabeza el nombre, para concordar después."""
    nucleo = base.split()[0]
    if nucleo in _FEMENINOS:
        return True
    if nucleo in _MASCULINOS:
        return False
    return nucleo.endswith("a")


def _componer(base: str, calificadores: list[str]) -> str:
    """Pega al nombre los calificadores conocidos. Los desconocidos se tiran.

    Dos como máximo: «arroz blanco integral en lata seco» no es un nombre, y a
    partir del segundo lo que se gana en precisión se pierde en legibilidad.
    """
    femenino = _es_femenino(base)
    extras: list[str] = []
    for segmento in calificadores:
        formas = _CALIFICADORES.get(segmento)
        if formas is None:
            continue
        traducido = formas[1] if femenino else formas[0]
        if traducido and traducido not in extras and traducido not in base:
            extras.append(traducido)
        if len(extras) == 2:
            break
    return " ".join([base, *extras]).strip()


def aliases_de(nombre: str) -> list[str]:
    """Cómo se llama el mismo alimento en otros países."""
    return list(_ALIASES.get(nombre, ()))
