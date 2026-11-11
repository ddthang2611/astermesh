from pathlib import Path

from astermesh.graph.builder import GraphBuilder
from astermesh.graph.model import EdgeType, NodeType


def test_ts_arrow_functions_calls_and_imports(tmp_path: Path):
    # arrange
    (tmp_path / "math.ts").write_text(
        """export const add = (a: number, b: number) => a + b;
export function compute(x: number) {
    return add(x, 1);
}
""",
        encoding="utf-8",
    )
    (tmp_path / "main.ts").write_text(
        """import { compute } from './math';
export const runApp = () => compute(10);
""",
        encoding="utf-8",
    )

    builder = GraphBuilder(tmp_path)

    # act
    g = builder.build_full()

    # assert: exported arrow functions and regular functions detected
    assert g.has_node("math.add")
    assert g.has_node("math.compute")
    assert g.has_node("main.runApp")
    assert g.nodes["math.add"]["is_exported"] is True

    # assert: call_expression creates CALLS edges (fixing degree=0)
    run_out = [(v, d["type"]) for _, v, d in g.out_edges("main.runApp", data=True)]
    assert ("math.compute", EdgeType.CALLS.value) in run_out

    comp_out = [(v, d["type"]) for _, v, d in g.out_edges("math.compute", data=True)]
    assert ("math.add", EdgeType.CALLS.value) in comp_out


def test_react_jsx_components_and_hooks(tmp_path: Path):
    # arrange
    (tmp_path / "useAuth.ts").write_text(
        """export const useAuth = () => ({ user: 'admin' });""",
        encoding="utf-8",
    )
    (tmp_path / "Badge.tsx").write_text(
        """export function Badge({ text }: { text: string }) {
    return <span>{text}</span>;
}""",
        encoding="utf-8",
    )
    (tmp_path / "Header.tsx").write_text(
        """import { useAuth } from './useAuth';
import { Badge } from './Badge';

export const Header = () => {
    const auth = useAuth();
    return <header><Badge text={auth.user} /></header>;
};
""",
        encoding="utf-8",
    )

    builder = GraphBuilder(tmp_path)

    # act
    g = builder.build_full()

    # assert: node types
    assert g.nodes["useAuth.useAuth"]["type"] == NodeType.HOOK.value
    assert g.nodes["Badge.Badge"]["type"] == NodeType.COMPONENT.value
    assert g.nodes["Header.Header"]["type"] == NodeType.COMPONENT.value

    # assert: CALLS hook + RENDERS child component
    out_edges = [(v, d["type"]) for _, v, d in g.out_edges("Header.Header", data=True)]
    assert ("useAuth.useAuth", EdgeType.CALLS.value) in out_edges
    assert ("Badge.Badge", EdgeType.RENDERS.value) in out_edges


def test_vue_and_svelte_sfc_parsing(tmp_path: Path):
    # arrange
    (tmp_path / "Avatar.vue").write_text(
        """<template><img src="x" /></template>
<script setup lang="ts">
const size = 32;
</script>
""",
        encoding="utf-8",
    )
    (tmp_path / "UserCard.vue").write_text(
        """<template>
  <div class="card"><user-avatar /></div>
</template>
<script setup lang="ts">
import UserAvatar from './Avatar.vue';
function onClick() { return true; }
</script>
""",
        encoding="utf-8",
    )
    (tmp_path / "Banner.svelte").write_text(
        """<script>
import UserAvatar from './Avatar.vue';
</script>
<section><UserAvatar /></section>
""",
        encoding="utf-8",
    )

    builder = GraphBuilder(tmp_path)

    # act
    g = builder.build_full()

    # assert
    assert g.has_node("UserCard.UserCard")
    assert g.nodes["UserCard.onClick"]["line_start"] == 6
    assert g.has_node("Banner.Banner")

    vc_edges = [(v, d["type"]) for _, v, d in g.out_edges("UserCard.UserCard", data=True)]
    assert ("Avatar.Avatar", EdgeType.RENDERS.value) in vc_edges

    sv_edges = [(v, d["type"]) for _, v, d in g.out_edges("Banner.Banner", data=True)]
    assert ("Avatar.Avatar", EdgeType.RENDERS.value) in sv_edges


def test_angular_decorators_and_di(tmp_path: Path):
    # arrange
    (tmp_path / "auth.service.ts").write_text(
        """@Injectable({ providedIn: 'root' })
export class AuthService {
    login() { return true; }
}""",
        encoding="utf-8",
    )
    (tmp_path / "login.html").write_text("<form></form>", encoding="utf-8")
    (tmp_path / "login.component.ts").write_text(
        """import { AuthService } from './auth.service';

@Component({
    selector: 'app-login',
    templateUrl: './login.html'
})
export class LoginComponent {
    constructor(private auth: AuthService) {}
    submit() { return this.auth.login(); }
}""",
        encoding="utf-8",
    )

    builder = GraphBuilder(tmp_path)

    # act
    g = builder.build_full()

    # assert
    svc_id = "auth.service.AuthService" if g.has_node("auth.service.AuthService") else next(
        n for n in g.nodes if n.endswith("AuthService")
    )
    comp_id = next(n for n in g.nodes if n.endswith("LoginComponent"))
    submit_id = next(n for n in g.nodes if n.endswith("LoginComponent.submit"))
    login_fn_id = next(n for n in g.nodes if n.endswith("AuthService.login"))

    assert g.nodes[svc_id]["type"] == NodeType.SERVICE.value
    assert g.nodes[comp_id]["type"] == NodeType.COMPONENT.value

    comp_edges = [(v, d["type"]) for _, v, d in g.out_edges(comp_id, data=True)]
    assert (svc_id, EdgeType.INJECTS.value) in comp_edges

    # assert constructor-injected member call resolves to AuthService.login
    submit_edges = [(v, d["type"]) for _, v, d in g.out_edges(submit_id, data=True)]
    assert (login_fn_id, EdgeType.CALLS.value) in submit_edges

    # assert templateUrl linked as lazy ASSET node
    assert g.has_node("login.html")
    assert g.nodes["login.html"]["type"] == NodeType.ASSET.value


# kiem thu import da dang, ke thua interface/class va class field arrow
def test_ts_imports_require_heritage_and_class_fields(tmp_path: Path):
    (tmp_path / "base.ts").write_text(
        """export class BaseRepo {}
export interface IRepo {}
export const helper = (x: number) => x + 1;
""",
        encoding="utf-8",
    )
    (tmp_path / "DefaultClient.ts").write_text(
        "export default class DefaultClient { ping() { return 'pong'; } }\n",
        encoding="utf-8",
    )
    (tmp_path / "legacy.js").write_text("function legacyRun() { return 1; }\n", encoding="utf-8")
    (tmp_path / "app.ts").write_text(
        """import DefaultClient from './DefaultClient';
import * as baseNs from './base';
import { helper as calcHelper } from './base';
import './legacy';
export { helper } from './base';
const legacyReq = require('./legacy');

export class UserRepo extends BaseRepo implements IRepo {
    run = () => {
        const c = new DefaultClient();
        baseNs.helper(1);
        return calcHelper(2);
    };
}
""",
        encoding="utf-8",
    )

    g = GraphBuilder(tmp_path).build_full()

    repo_edges = [(v, d["type"]) for _, v, d in g.out_edges("app.UserRepo", data=True)]
    assert ("base.BaseRepo", EdgeType.INHERITS.value) in repo_edges
    assert ("base.IRepo", EdgeType.INHERITS.value) in repo_edges

    run_edges = [(v, d["type"]) for _, v, d in g.out_edges("app.UserRepo.run", data=True)]
    assert ("DefaultClient.DefaultClient", EdgeType.CALLS.value) in run_edges
    assert ("base.helper", EdgeType.CALLS.value) in run_edges

    file_edges = [(v, d["type"]) for _, v, d in g.out_edges("app.ts", data=True)]
    assert ("legacy.js", EdgeType.IMPORTS.value) in file_edges


# kiem thu react context provider, hoc memo, fragment va ham pascal khong jsx
def test_react_provider_hoc_fragment_and_non_jsx(tmp_path: Path):
    (tmp_path / "ThemeContext.tsx").write_text(
        "export const ThemeContext = { Provider: (p: any) => p.children };",
        encoding="utf-8",
    )
    (tmp_path / "Leaf.tsx").write_text(
        """const memo = (fn: any) => fn;
export const Leaf = memo(() => <><span>ok</span></>);
export function FormatLabel(v: string) { return v.trim(); }
""",
        encoding="utf-8",
    )
    (tmp_path / "Panel.tsx").write_text(
        """import { ThemeContext } from './ThemeContext';
import { Leaf } from './Leaf';

export function Panel() {
    return (
        <ThemeContext.Provider>
            <div><Leaf /></div>
        </ThemeContext.Provider>
    );
}
""",
        encoding="utf-8",
    )

    g = GraphBuilder(tmp_path).build_full()

    assert g.nodes["Leaf.Leaf"]["type"] == NodeType.COMPONENT.value
    assert g.nodes["Leaf.FormatLabel"]["type"] == NodeType.FUNCTION.value

    panel_edges = [(v, d["type"]) for _, v, d in g.out_edges("Panel.Panel", data=True)]
    assert ("ThemeContext.ThemeContext", EdgeType.PROVIDES.value) in panel_edges
    assert ("Leaf.Leaf", EdgeType.RENDERS.value) in panel_edges


# kiem thu svelte khong co script va angular ngmodule provides
def test_svelte_markup_only_and_angular_ngmodule_provides(tmp_path: Path):
    (tmp_path / "PureBadge.svelte").write_text(
        "<span><slot /></span>\n",
        encoding="utf-8",
    )
    (tmp_path / "ConfigService.ts").write_text(
        """@Injectable()
export class ConfigService<T> {
    get() { return 1; }
}
""",
        encoding="utf-8",
    )
    (tmp_path / "card.css").write_text(".card {}", encoding="utf-8")
    (tmp_path / "CardComponent.ts").write_text(
        """import { ConfigService } from './ConfigService';
@Component({
    selector: 'app-card',
    styleUrls: ['./card.css']
})
export class CardComponent {
    constructor(private cfg?: ConfigService<string>, private label: string = '') {}
}
""",
        encoding="utf-8",
    )
    (tmp_path / "AppModule.ts").write_text(
        """import { CardComponent } from './CardComponent';
import { ConfigService } from './ConfigService';
@NgModule({
    declarations: [CardComponent],
    providers: [ConfigService]
})
export class AppModule {}
""",
        encoding="utf-8",
    )

    g = GraphBuilder(tmp_path).build_full()

    assert g.nodes["PureBadge.PureBadge"]["type"] == NodeType.COMPONENT.value
    assert g.nodes["AppModule.AppModule"]["type"] == NodeType.MODULE.value
    assert g.has_node("card.css")

    mod_edges = [(v, d["type"]) for _, v, d in g.out_edges("AppModule.AppModule", data=True)]
    assert ("CardComponent.CardComponent", EdgeType.PROVIDES.value) in mod_edges
    assert ("ConfigService.ConfigService", EdgeType.PROVIDES.value) in mod_edges

    card_edges = [(v, d["type"]) for _, v, d in g.out_edges("CardComponent.CardComponent", data=True)]
    assert ("ConfigService.ConfigService", EdgeType.INJECTS.value) in card_edges
