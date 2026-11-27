from pathlib import Path

from astermesh.graph.builder import GraphBuilder
from astermesh.graph.clustering import CommunityClusterer
from astermesh.graph.filters import is_java_test_path
from astermesh.graph.model import EdgeType, NodeType
from astermesh.languages.java.plugin import JavaLanguagePlugin
from astermesh.serializers.compact_wire import render_compact_wire


def _edge_types(graph, u: str, v: str) -> set[str]:
    if not graph.has_node(u) or not graph.has_node(v):
        return set()
    data = graph.get_edge_data(u, v) or {}
    return {str(d.get("type")) for d in data.values()}


# java 8-21 syntax, 4 imports, 3-tier overloads
def test_java_syntax_imports_constructors_and_three_tier_overloads(tmp_path: Path) -> None:
    # arrange
    pkg_dir = tmp_path / "src" / "main" / "java" / "com" / "acme"
    util_dir = pkg_dir / "util"
    pkg_dir.mkdir(parents=True)
    util_dir.mkdir(parents=True)

    (util_dir / "MathUtil.java").write_text(
        """package com.acme.util;

public class MathUtil {
    public static int add(int a, int b) { return a + b; }
    public static int mul(int a, int b) { return a * b; }
}
""",
        encoding="utf-8",
    )
    (util_dir / "Tag.java").write_text(
        """package com.acme.util;

public @interface Tag {
    String value() default "";
}
""",
        encoding="utf-8",
    )
    (pkg_dir / "Role.java").write_text(
        """package com.acme;

public enum Role {
    ADMIN, USER;
    public boolean isPrivileged() { return this == ADMIN; }
}
""",
        encoding="utf-8",
    )
    (pkg_dir / "Auditable.java").write_text(
        """package com.acme;

public interface Auditable {
    void audit(String msg);
}
""",
        encoding="utf-8",
    )
    (pkg_dir / "OrderRecord.java").write_text(
        """package com.acme;

public record OrderRecord(String id, int amount) {
    public OrderRecord {
        if (amount < 0) {
            throw new IllegalArgumentException();
        }
    }
}
""",
        encoding="utf-8",
    )
    (pkg_dir / "ComplexProcessor.java").write_text(
        """package com.acme;

import java.util.List;
import com.acme.util.*;
import com.acme.OrderRecord;
import static com.acme.util.MathUtil.add;
import static com.acme.util.MathUtil.*;

@Tag("core")
public class ComplexProcessor implements Auditable {
    public static class NestedHelper {
        public void ping() {}
    }

    public ComplexProcessor() {}
    public ComplexProcessor(String name) {}

    public void single() {
        int s = add(1, 2);
        int p = mul(s, 3);
        new NestedHelper().ping();
    }

    public void process(String a) {}
    public void process(String a, int b) {}

    public void save(List<String> items, int[] counts) {}
    public void save(Role role, String... tags) {}

    public void collide(java.util.Date d) {}
    public void collide(java.sql.Date d) {}

    @Override
    public void audit(String msg) {}
}
""",
        encoding="utf-8",
    )

    # act
    plugin = JavaLanguagePlugin()
    proc_path = pkg_dir / "ComplexProcessor.java"
    parsed = plugin.parse_file(
        proc_path.read_bytes(),
        "src/main/java/com/acme/ComplexProcessor.java",
        tmp_path,
    )
    assert parsed is not None

    # assert: 4 import forms
    assert parsed.namespace_fqn == "com.acme"
    assert parsed.imports["OrderRecord"] == "com.acme.OrderRecord"
    assert parsed.wildcard_imports == ["com.acme.util"]
    assert parsed.static_imports["add"] == "com.acme.util.MathUtil.add"
    assert parsed.static_wildcards == ["com.acme.util.MathUtil"]

    fqns = {e.fqn for e in parsed.entities}
    # nested class & constructors & 3-tier overload fqns
    assert "com.acme.ComplexProcessor.NestedHelper" in fqns
    assert "com.acme.ComplexProcessor.NestedHelper.ping" in fqns
    assert "com.acme.ComplexProcessor.$init#0" in fqns
    assert "com.acme.ComplexProcessor.$init#1" in fqns
    assert "com.acme.ComplexProcessor.single" in fqns
    assert "com.acme.ComplexProcessor.process#1" in fqns
    assert "com.acme.ComplexProcessor.process#2" in fqns
    assert "com.acme.ComplexProcessor.save(List/intArr)" in fqns
    assert "com.acme.ComplexProcessor.save(Role/StringArr)" in fqns

    collide_fqns = sorted(
        f for f in fqns if f.startswith("com.acme.ComplexProcessor.collide(Date)")
    )
    assert len(collide_fqns) == 2
    assert any("#L" in f for f in collide_fqns)

    # acn2 reserved delimiter safety on all fqns
    for fqn in fqns:
        assert not any(ch in fqn for ch in (",", "@", ":", ">"))

    # verify full graph linking and record/enum/@interface nodes
    graph = GraphBuilder(tmp_path).build_full()
    assert graph.nodes["com.acme.Role"]["type"] == NodeType.CLASS.value
    assert graph.nodes["com.acme.Auditable"]["type"] == NodeType.CLASS.value
    assert graph.nodes["com.acme.util.Tag"]["type"] == NodeType.CLASS.value
    assert graph.nodes["com.acme.OrderRecord"]["type"] == NodeType.CLASS.value
    assert graph.has_node("com.acme.OrderRecord.$init")

    assert EdgeType.CALLS.value in _edge_types(
        graph, "com.acme.ComplexProcessor.single", "com.acme.util.MathUtil.add"
    )
    assert EdgeType.CALLS.value in _edge_types(
        graph, "com.acme.ComplexProcessor.single", "com.acme.util.MathUtil.mul"
    )
    assert EdgeType.CALLS.value in _edge_types(
        graph, "com.acme.ComplexProcessor.single", "com.acme.ComplexProcessor.NestedHelper.ping"
    )
    assert EdgeType.INHERITS.value in _edge_types(
        graph, "com.acme.ComplexProcessor", "com.acme.Auditable"
    )
    assert EdgeType.INHERITS.value in _edge_types(
        graph, "com.acme.ComplexProcessor.audit", "com.acme.Auditable.audit"
    )

    # verify acn2 short ids remain delimiter-safe
    comms = CommunityClusterer(seed=0).cluster(graph)
    wire = render_compact_wire(graph, comms, "java_app")
    assert "#ACN2" in wire


# spring stereotypes, lombok, immutables, guice, chains, override
def test_java_di_codegen_chains_and_override(tmp_path: Path) -> None:
    # arrange
    base_dir = tmp_path / "src" / "main" / "java" / "com" / "shop"
    base_dir.mkdir(parents=True)

    (base_dir / "PaymentGateway.java").write_text(
        """package com.shop;

public interface PaymentGateway {
    Receipt charge(String orderId);
}
""",
        encoding="utf-8",
    )
    (base_dir / "StripeGateway.java").write_text(
        """package com.shop;

public class StripeGateway implements PaymentGateway {
    @Override
    public Receipt charge(String orderId) {
        return new Receipt();
    }
}
""",
        encoding="utf-8",
    )
    (base_dir / "Receipt.java").write_text(
        """package com.shop;

public class Receipt {
    public AuditTrail audit() { return new AuditTrail(); }
}
""",
        encoding="utf-8",
    )
    (base_dir / "AuditTrail.java").write_text(
        """package com.shop;

public class AuditTrail {
    public Summary summarize() { return new Summary(); }
}
""",
        encoding="utf-8",
    )
    (base_dir / "Summary.java").write_text(
        """package com.shop;

public class Summary {
    public void publish() {}
}
""",
        encoding="utf-8",
    )
    (base_dir / "OrderModel.java").write_text(
        """package com.shop;

import org.immutables.value.Value;

@Value.Immutable
public interface OrderModel {
    String id();
}
""",
        encoding="utf-8",
    )
    (base_dir / "CustomerDto.java").write_text(
        """package com.shop;

import lombok.Builder;
import lombok.Data;

@Data
@Builder
public class CustomerDto {
    private String name;
    private boolean active;
}
""",
        encoding="utf-8",
    )
    (base_dir / "InventoryRepo.java").write_text(
        """package com.shop;

import org.springframework.stereotype.Repository;

@Repository
public class InventoryRepo {
    public void reserve() {}
}
""",
        encoding="utf-8",
    )
    (base_dir / "OrderService.java").write_text(
        """package com.shop;

import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class OrderService {
    private final InventoryRepo inventoryRepo;
    private final PaymentGateway gateway;
    private static final String TAG = "v1";

    public void submit(CustomerDto dto) {
        inventoryRepo.reserve();
        dto.setName("Alice");
        String n = dto.getName();
        boolean ok = dto.isActive();
        CustomerDto.builder();
        ImmutableOrderModel.builder();
        gateway.charge(n).audit().summarize().publish();
    }
}
""",
        encoding="utf-8",
    )
    (base_dir / "OrderController.java").write_text(
        """package com.shop;

import jakarta.inject.Inject;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class OrderController {
    @Autowired
    private OrderService orderService;

    private final AuditTrail trail;

    @Inject
    public OrderController(AuditTrail trail) {
        this.trail = trail;
    }

    public void create(CustomerDto dto) {
        orderService.submit(dto);
    }
}
""",
        encoding="utf-8",
    )
    (base_dir / "AllArgsBean.java").write_text(
        """package com.shop;

import lombok.AllArgsConstructor;
import org.springframework.stereotype.Component;

@Component
@AllArgsConstructor
public class AllArgsBean {
    private Summary summary;
    private static int counter;
}
""",
        encoding="utf-8",
    )
    (base_dir / "AppConfig.java").write_text(
        """package com.shop;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class AppConfig {
    @Bean
    public PaymentGateway paymentGateway() {
        return new StripeGateway();
    }
}
""",
        encoding="utf-8",
    )
    (base_dir / "BillingModule.java").write_text(
        """package com.shop;

import com.google.inject.AbstractModule;
import com.google.inject.Provides;

public class BillingModule extends AbstractModule {
    @Override
    protected void configure() {
        bind(PaymentGateway.class).to(StripeGateway.class);
    }

    @Provides
    public Summary provideSummary() {
        return new Summary();
    }
}
""",
        encoding="utf-8",
    )

    # act
    graph = GraphBuilder(tmp_path).build_full()

    # assert: stereotypes and module classification
    assert graph.nodes["com.shop.OrderService"]["type"] == NodeType.SERVICE.value
    assert graph.nodes["com.shop.OrderController"]["type"] == NodeType.SERVICE.value
    assert graph.nodes["com.shop.InventoryRepo"]["type"] == NodeType.SERVICE.value
    assert graph.nodes["com.shop.AllArgsBean"]["type"] == NodeType.SERVICE.value
    assert graph.nodes["com.shop.AppConfig"]["type"] == NodeType.MODULE.value
    assert graph.nodes["com.shop.BillingModule"]["type"] == NodeType.MODULE.value

    # assert: Spring @Autowired / @Inject and @Bean
    assert EdgeType.INJECTS.value in _edge_types(
        graph, "com.shop.OrderController", "com.shop.OrderService"
    )
    assert EdgeType.INJECTS.value in _edge_types(
        graph, "com.shop.OrderController", "com.shop.AuditTrail"
    )
    assert EdgeType.PROVIDES.value in _edge_types(
        graph, "com.shop.AppConfig.paymentGateway", "com.shop.PaymentGateway"
    )

    # assert: Lombok @RequiredArgsConstructor and @AllArgsConstructor
    assert EdgeType.INJECTS.value in _edge_types(
        graph, "com.shop.OrderService", "com.shop.InventoryRepo"
    )
    assert EdgeType.INJECTS.value in _edge_types(
        graph, "com.shop.OrderService", "com.shop.PaymentGateway"
    )
    assert EdgeType.INJECTS.value in _edge_types(
        graph, "com.shop.AllArgsBean", "com.shop.Summary"
    )

    # assert: Lombok @Data/@Getter/@Setter -> REFERENCES on fields, @Builder -> CALLS
    assert EdgeType.REFERENCES.value in _edge_types(
        graph, "com.shop.OrderService.submit", "com.shop.CustomerDto.name"
    )
    assert EdgeType.REFERENCES.value in _edge_types(
        graph, "com.shop.OrderService.submit", "com.shop.CustomerDto.active"
    )
    assert EdgeType.CALLS.value in _edge_types(
        graph, "com.shop.OrderService.submit", "com.shop.CustomerDto"
    )

    # assert: Immutables @Value.Immutable -> ImmutableOrderModel resolves to OrderModel
    assert EdgeType.CALLS.value in _edge_types(
        graph, "com.shop.OrderService.submit", "com.shop.OrderModel"
    )

    # assert: Guice bind().to() and @Provides
    assert EdgeType.PROVIDES.value in _edge_types(
        graph, "com.shop.BillingModule.configure", "com.shop.PaymentGateway"
    )
    assert EdgeType.PROVIDES.value in _edge_types(
        graph, "com.shop.BillingModule.configure", "com.shop.StripeGateway"
    )
    assert EdgeType.PROVIDES.value in _edge_types(
        graph, "com.shop.BillingModule.provideSummary", "com.shop.Summary"
    )

    # assert: 3-hop call chain via return_type
    assert EdgeType.CALLS.value in _edge_types(
        graph, "com.shop.OrderService.submit", "com.shop.PaymentGateway.charge"
    )
    assert EdgeType.CALLS.value in _edge_types(
        graph, "com.shop.OrderService.submit", "com.shop.Receipt.audit"
    )
    assert EdgeType.CALLS.value in _edge_types(
        graph, "com.shop.OrderService.submit", "com.shop.AuditTrail.summarize"
    )
    assert EdgeType.CALLS.value in _edge_types(
        graph, "com.shop.OrderService.submit", "com.shop.Summary.publish"
    )

    # assert: @Override -> SubClass.method --INHERITS--> Base.method
    assert EdgeType.INHERITS.value in _edge_types(
        graph, "com.shop.StripeGateway.charge", "com.shop.PaymentGateway.charge"
    )


# product-to-test boundary in same package
def test_java_test_path_patterns_and_same_package_blocking(tmp_path: Path) -> None:
    # arrange
    assert is_java_test_path("src/test/java/com/acme/OrderTest.java")
    assert is_java_test_path("mod/integration-tests/src/main/java/Helper.java")
    assert is_java_test_path("fineract-e2e-tests-core/src/Helper.java")
    assert is_java_test_path("module-test/src/Helper.java")
    assert is_java_test_path("module-tests/src/Helper.java")
    assert is_java_test_path("com/acme/OrderTests.java")
    assert is_java_test_path("com/acme/OrderIT.java")
    assert is_java_test_path("com/acme/OrderSpec.java")
    assert is_java_test_path("com/acme/OrderTestBase.java")
    assert is_java_test_path("com/acme/AllSuite.java")
    assert is_java_test_path("com/acme/FastSuiteNoDB.java")
    assert not is_java_test_path("src/main/java/com/acme/OrderService.java")

    prod_dir = tmp_path / "src" / "main" / "java" / "com" / "acme"
    test_dir = tmp_path / "src" / "test" / "java" / "com" / "acme"
    prod_dir.mkdir(parents=True)
    test_dir.mkdir(parents=True)

    (prod_dir / "ProdCaller.java").write_text(
        """package com.acme;

public class ProdCaller {
    private TestFixtureHelper helper;

    public void run() {
        helper.seed();
    }
}
""",
        encoding="utf-8",
    )
    (test_dir / "TestFixtureHelper.java").write_text(
        """package com.acme;

public class TestFixtureHelper {
    public void seed() {}
}
""",
        encoding="utf-8",
    )
    (test_dir / "ProdCallerTest.java").write_text(
        """package com.acme;

public class ProdCallerTest {
    private ProdCaller caller = new ProdCaller();
    private TestFixtureHelper helper = new TestFixtureHelper();

    public void verify() {
        caller.run();
        helper.seed();
    }
}
""",
        encoding="utf-8",
    )

    # act
    graph = GraphBuilder(tmp_path).build_full()

    # assert: prod cannot link to same-package test node, test can link to prod and test
    assert not graph.has_edge("com.acme.ProdCaller.run", "com.acme.TestFixtureHelper.seed")
    assert EdgeType.CALLS.value in _edge_types(
        graph, "com.acme.ProdCallerTest.verify", "com.acme.ProdCaller.run"
    )
    assert EdgeType.CALLS.value in _edge_types(
        graph, "com.acme.ProdCallerTest.verify", "com.acme.TestFixtureHelper.seed"
    )


# field vs same-named method #field disambiguation
def test_java_field_method_collision_disambiguation_and_acn2_l0(tmp_path: Path) -> None:
    # arrange
    pkg_dir = tmp_path / "src" / "main" / "java" / "p"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "Dto.java").write_text(
        """package p;

public class Dto {
    private final String clientId;

    public Dto(String clientId) {
        this.clientId = clientId;
    }

    public String clientId() {
        return clientId;
    }
}
""",
        encoding="utf-8",
    )

    # act
    graph = GraphBuilder(tmp_path).build_full()
    comms = CommunityClusterer(seed=0).cluster(graph)
    wire = render_compact_wire(graph, comms, "dto_repo")

    # assert: both field and method preserved with distinct FQNs
    assert graph.has_node("p.Dto.clientId#field")
    assert graph.has_node("p.Dto.clientId")
    assert graph.nodes["p.Dto.clientId#field"]["type"] == NodeType.VARIABLE.value
    assert graph.nodes["p.Dto.clientId#field"]["line_start"] == 4
    assert graph.nodes["p.Dto.clientId"]["type"] == NodeType.FUNCTION.value
    assert graph.nodes["p.Dto.clientId"]["line_start"] == 10

    # assert: ACN2 L0 renders both cleanly without delimiter corruption
    assert wire.startswith("#ACN2 root:dto_repo mode:L0")
    assert "clientId#field:v@4" in wire
    assert "clientId:f@10-12" in wire

