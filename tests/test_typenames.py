from nodecanvas_cli.typenames import TypeNames
from nodecanvas_cli.workspace import Workspace


def test_unity_types_get_their_module(project):
    t = TypeNames(Workspace(project))
    asm = lambda name: t.qualify(name).split(", ")[1]  # noqa: E731
    assert asm("Vector3") == "UnityEngine.CoreModule"
    assert asm("GameObject") == "UnityEngine.CoreModule"
    assert asm("AudioClip") == "UnityEngine.AudioModule"
    assert asm("Rigidbody") == "UnityEngine.PhysicsModule"
    assert asm("Rigidbody2D") == "UnityEngine.Physics2DModule"
    assert asm("Animator") == "UnityEngine.AnimationModule"
    assert t.full("List<Rigidbody>").endswith(
        "[[UnityEngine.Rigidbody, UnityEngine.PhysicsModule, Version=0.0.0.0, Culture=neutral, PublicKeyToken=null]]"
    )
