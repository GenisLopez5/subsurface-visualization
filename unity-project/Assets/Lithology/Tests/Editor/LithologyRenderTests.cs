using NUnit.Framework;
using Subsurface.Lithology.Editor;
using UnityEditor;
using UnityEngine;

namespace Subsurface.Lithology.Tests
{
    public class LithologyRenderTests
    {
        GameObject sample,cameraObject,lightObject;
        Camera camera;
        RenderTexture target;
        LithologyCatalog c;
        bool previousAsyncCompilation;
        [SetUp]public void Setup()
        {
            Assert.That(SystemInfo.graphicsDeviceType,Is.Not.EqualTo(UnityEngine.Rendering.GraphicsDeviceType.Null),"These are GPU tests; do not run -nographics");
            previousAsyncCompilation=ShaderUtil.allowAsyncCompilation;ShaderUtil.allowAsyncCompilation=false;
            c=LithologyBuilder.Catalog;sample=GameObject.CreatePrimitive(PrimitiveType.Cube);sample.layer=31;sample.transform.position=new Vector3(1000,1000,1000);sample.transform.localScale=new Vector3(1.8f,1.8f,1.8f);
            cameraObject=new GameObject("Lithology GPU verification camera");camera=cameraObject.AddComponent<Camera>();camera.cullingMask=1<<31;camera.clearFlags=CameraClearFlags.SolidColor;camera.backgroundColor=Color.black;camera.orthographic=true;camera.orthographicSize=1.35f;cameraObject.transform.position=sample.transform.position+new Vector3(0,.6f,-4);cameraObject.transform.LookAt(sample.transform);camera.nearClipPlane=.1f;camera.farClipPlane=20;
            lightObject=new GameObject("Lithology GPU verification light");var light=lightObject.AddComponent<Light>();light.type=LightType.Directional;light.intensity=1.5f;light.cullingMask=1<<31;light.transform.rotation=Quaternion.Euler(35,-30,0);
            target=new RenderTexture(192,192,24,RenderTextureFormat.ARGB32);target.Create();camera.targetTexture=target;
        }
        [TearDown]public void Cleanup()
        {ShaderUtil.allowAsyncCompilation=previousAsyncCompilation;camera.targetTexture=null;target.Release();Object.DestroyImmediate(target);Object.DestroyImmediate(sample);Object.DestroyImmediate(cameraObject);Object.DestroyImmediate(lightObject);}
        Color32[] Render(Material m)
        {
            ShaderUtil.CompilePass(m,0,true);sample.GetComponent<Renderer>().sharedMaterial=m;camera.Render();var old=RenderTexture.active;RenderTexture.active=target;var t=new Texture2D(192,192,TextureFormat.RGBA32,false);t.ReadPixels(new Rect(0,0,192,192),0,0);t.Apply();var pixels=t.GetPixels32();Object.DestroyImmediate(t);RenderTexture.active=old;return pixels;
        }
        static float Difference(Color32[] a,Color32[] b)
        {long sum=0;for(int i=0;i<a.Length;i++)sum+=System.Math.Abs(a[i].r-b[i].r)+System.Math.Abs(a[i].g-b[i].g)+System.Math.Abs(a[i].b-b[i].b);return sum/(float)(a.Length*3);}
        [Test]public void TexturedUnknownUsesRoughShadingInsteadOfTheFlatLegendPattern()
        {
            using(var cache=new LithologyMaterialCache())
            using(var texture=cache.Acquire(c,null,DisplayMode.Texture))
            using(var legend=cache.Acquire(c,null,DisplayMode.Legend))
            {Assert.That(Difference(Render(texture.Material),Render(legend.Material)),Is.GreaterThan(.15f));}
        }
        [TestCase("Silt")][TestCase("Clay Shale")]
        public void FineSurfaceDetailIsVisibleOnSiltAndUnmappedClayShale(string label)
        {
            using(var cache=new LithologyMaterialCache())
            using(var texture=cache.Acquire(c,c.Resolve(label).definition,DisplayMode.Texture))
            {
                var visible=Render(texture.Material);texture.Material.SetFloat("_FineSurfaceStrength",0);
                Assert.That(Difference(visible,Render(texture.Material)),Is.GreaterThan(.15f),"Fine surface detail does not contribute to the image");
            }
        }
        [Test]public void ZeroComponentIsPixelIdenticalAndPositiveComponentChangesPixels()
        {
            using(var cache=new LithologyMaterialCache())
            {
                var d=c.Resolve("Sandstone").definition;
                var visual=new VisualOverrides{exaggerationMultiplier=30};
                using(var host=cache.Acquire(c,d,DisplayMode.Texture,visual))using(var zero=cache.Acquire(c,d,DisplayMode.Texture,visual,new[]{new ModifierSettings{id="pyrite_component",strength=0,occurrence=MaskKind.Crystal}}))
                using(var component=cache.Acquire(c,d,DisplayMode.Texture,visual,new[]{new ModifierSettings{id="pyrite_component",strength=1,occurrence=MaskKind.Crystal}}))
                {
                    var a=Render(host.Material);var b=Render(zero.Material);var cc=Render(component.Material);
                    Assert.That(Difference(a,b),Is.LessThan(.01f));Assert.That(Difference(a,cc),Is.GreaterThan(.1f));
                    int pink=0;foreach(var p in a)if(p.r>220&&p.b>220&&p.g<30)pink++;Assert.That(pink,Is.Zero,"Error shader detected");
                }
            }
        }
        [Test]public void ObjectRestPatternsFollowTranslationAndHandleNegativeNonuniformScale()
        {
            using(var cache=new LithologyMaterialCache())
            using(var lease=cache.Acquire(c,c.Resolve("Chert - Tiger Stripe").definition,DisplayMode.Texture,new VisualOverrides{overrideCoordinates=true,coordinates=new CoordinateSettings{space=ProjectionSpace.ObjectRest}}))
            {
                sample.transform.localScale=new Vector3(-1.8f,1.2f,2);var a=Render(lease.Material);sample.transform.position+=new Vector3(.37f,.19f,.23f);camera.transform.position+=new Vector3(.37f,.19f,.23f);var b=Render(lease.Material);
                Assert.That(Difference(a,b),Is.LessThan(.6f),"Object-rest coordinates drift when translated");
            }
        }
    }
}
