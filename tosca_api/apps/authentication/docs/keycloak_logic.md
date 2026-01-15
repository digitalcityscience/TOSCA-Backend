```mermaid
sequenceDiagram
    participant U as User (Browser)
    participant D as Django App
    participant K as Keycloak

    U->>D: GET /accounts/login/
    D->>U: login.html (Keycloak button)

    U->>D: GET /accounts/oidc/keycloak/login/
    D->>U: 302 Redirect to Keycloak

    U->>K: Keycloak Login Page
    K->>U: Login form
    U->>K: Username / password
    K->>K: Authentication
    K->>U: 302 Redirect + authorization code

    U->>D: GET /accounts/oidc/keycloak/login/callback/?code=xxx

    Note over D: allauth OAuth2CallbackView
    D->>K: Token exchange (code → tokens)
    K->>D: access_token, id_token

    Note over D: KeycloakAdapter.pre_social_login()

    alt Existing Social Account
        D->>D: is_existing: True
        D->>D: Update roles
    else New User
        D->>D: is_existing: False
        D->>D: Look up user by username
        alt User found
            D->>D: sociallogin.connect()
        else User not found
            D->>D: User.objects.create()
            D->>D: sociallogin.user = new_user
        end
        D->>D: Apply roles (ADMIN / SUPERADMIN)
    end

    Note over D: NoSignupAccountAdapter.get_login_redirect_url()

    alt is_staff = True
        D->>U: 302 Redirect /admin/
    else is_staff = False
        D->>U: 302 Redirect /welcome/
    end

    U->>D: GET /welcome/ or /admin/
    D->>U: Page content
```

````mermaid
flowchart TB
    subgraph Django["Django App"]
        subgraph URLs["urls.py"]
            L["/accounts/login/"]
            C["/accounts/oidc/keycloak/login/callback/"]
            W["/welcome/"]
            A["/admin/"]
        end

        subgraph Views["views.py"]
            KRV["KeycloakRedirectView<br/>Show login.html"]
            WV["welcome_view<br/>Show welcome.html"]
        end

        subgraph Backends["backends.py"]
            KA["KeycloakAdapter<br/>(SocialAccountAdapter)"]
            NSA["NoSignupAccountAdapter<br/>(AccountAdapter)"]
            KTA["KeycloakTokenAuthentication<br/>(for API)"]
        end

        subgraph Adapters["Adapter Methods"]
            PSL["pre_social_login()<br/>• Create/Find User<br/>• Apply Roles"]
            GLR["get_login_redirect_url()<br/>• Admin → /admin/<br/>• User → /welcome/"]
        end
    end

    subgraph Keycloak["Keycloak"]
        KC["Auth Server"]
        OIDC["OIDC Provider"]
    end

    L --> KRV
    C --> KA
    KA --> PSL
    PSL --> GLR
    GLR --> W
    GLR --> A
    W --> WV
    ```
````
