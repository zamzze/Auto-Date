# 📱 Automatización Android – Entorno Completo (Appium + Python)

Este proyecto utiliza **Appium + Python** para automatizar un **dispositivo Android físico** (NO emuladores).

Este README existe para responder, de una vez por todas:

- Qué **sí es obligatorio**
- Qué **es opcional**
- Qué **NO necesitas** (aunque lo hayas instalado)

Si clonas este repo en una PC nueva, siguiendo esta guía **todo debería funcionar sin adivinar nada**.

---

## 🧠 Arquitectura general (para entender qué hace qué)

```text
Python (tu código)
   │
   ▼
Appium (servidor)
   │
   ▼
UIAutomator2 (driver Android)
   │
   ▼
ADB (Android Debug Bridge)
   │
   ▼
Dispositivo Android físico
```

👉 **Python NO controla el celular directamente**. Solo le manda órdenes a Appium.

---

## ✅ ¿Necesito Android Studio?

**❌ NO es obligatorio**.

### Cuándo SÍ sirve Android Studio

- Crear / usar **emuladores**
- Depurar apps como desarrollador
- Programar apps Android

### Cuándo NO lo necesitas (este proyecto)

- Solo usas **dispositivo físico**
- No desarrollas apps Android
- Solo necesitas **ADB + SDK tools**

👉 Si ya lo tienes instalado: **no molesta** 👉 Si no lo tienes: **no lo instales**

---

## 🔧 ¿Para qué sirven los SDK entonces?

Aunque NO uses emulador, **sigues necesitando**:

### Android SDK Platform Tools

Incluye:

- `adb` (OBLIGATORIO)

ADB es lo que permite:

- Detectar el celular
- Instalar el servidor de Appium en el dispositivo
- Ejecutar acciones (tap, swipe, input, etc.)

📌 **Sin ADB, Appium no existe**.

---

## 🧱 Requisitos del sistema (OBLIGATORIOS)

### 1️⃣ Sistema operativo

- Windows / macOS / Linux

---

### 2️⃣ Python

- Versión recomendada: **Python 3.9 – 3.11**

Verificar:

```bash
python --version
pip --version
```

---

### 3️⃣ Node.js (Appium depende de esto)

- Versión recomendada: **Node.js LTS (18 o 20)**

Verificar:

```bash
node -v
npm -v
```

---

### 4️⃣ Appium (global)

Instalar:

```bash
npm install -g appium
```

Verificar:

```bash
appium -v
```

---

### 5️⃣ Driver Android – UIAutomator2 (OBLIGATORIO)

Instalar:

```bash
appium driver install uiautomator2
```

Verificar:

```bash
appium driver list
```

Debe aparecer:

```text
uiautomator2  ✓ installed
```

---

### 6️⃣ Android SDK Platform Tools (ADB)

Necesitas **solo esto**, NO Android Studio completo.

Verificar:

```bash
adb version
adb devices
```

Si el celular aparece en la lista → todo bien.

---

## 📱 Requisitos del dispositivo Android

En el celular:

- ✔ Opciones de desarrollador activadas
- ✔ Depuración USB activada
- ✔ Autorizar la PC cuando lo pida
- ✔ Realizar Root
- ✔ Pantalla desbloqueada (recomendado)

---

## 🐍 Requisitos Python del proyecto

Archivo `requirements.txt`:

```txt
appium-python-client
requests
pandas
```

Instalar:

```bash
pip install -r requirements.txt
```

---

## 🚀 Orden correcto de instalación (PC limpia)

1. Instalar **Python**
2. Instalar **Node.js LTS**
3. Instalar **Android SDK Platform Tools** (ADB)
4. Instalar **Appium**
5. Instalar **UIAutomator2 driver**
6. Clonar el repo
7. Instalar requirements Python
8. Conectar celular rooteado
9. Ejecutar Appium
10. Correr el script

---

## ▶️ Ejecutar Appium

```bash
appium
```

Por defecto corre en:

```
http://127.0.0.1:4723
```

---

## ▶️ Ejecutar la automatización

Ejemplo:

```bash
python main.py
```

(Ajustar según el entrypoint real del proyecto)

---

## ❌ Cosas que NO necesitas

- ❌ Selenium
- ❌ Android Studio (si usas solo físico)
- ❌ Emuladores
- ❌ APK propia

---

## ✅ Cosas opcionales pero útiles

- `venv` / `virtualenv`
- `.env` para variables
- Logs (`logging`, `loguru`)

---

## 🧠 Resumen rápido

| Componente      | ¿Necesario? |
| --------------- | ----------- |
| Python          | ✅ Sí        |
| Node.js         | ✅ Sí        |
| Appium          | ✅ Sí        |
| UIAutomator2    | ✅ Sí        |
| ADB / SDK tools | ✅ Sí        |
| Android Studio  | ❌ No        |
| Emulador        | ❌ No        |

---

Si algo de esto falla, **el problema NO está en tu código**, sino en el entorno.

Bienvenido al mundo real de la automatización 😄🔥

