using UnityEngine;
namespace Subsurface.Borehole
{
    public sealed class BoreholeCutaway:MonoBehaviour
    {
        BoreholeColumn column;Transform interior;
        public float SurfaceOffset=>column!=null&&column.Document!=null&&column.Document.TopFeet>0?3f:0f;
        GameObject ground;Material soil,grass,sky;Camera camera;CameraClearFlags previousFlags;Skybox box;bool addedBox;Material previousSky;
        public void Initialize(BoreholeColumn column,Camera target)
        {
            if(ground!=null)return;this.column=column;camera=target;previousFlags=camera.clearFlags;camera.clearFlags=CameraClearFlags.Skybox;
            box=camera.GetComponent<Skybox>();if(box==null){box=camera.gameObject.AddComponent<Skybox>();addedBox=true;}previousSky=box.material;
            sky=new Material(Shader.Find("Skybox/Procedural")){name="Borehole skybox",hideFlags=HideFlags.DontSave};sky.SetFloat("_SunSize",.025f);sky.SetFloat("_AtmosphereThickness",1);box.material=sky;
            soil=new Material(Shader.Find("Universal Render Pipeline/Lit")){name="Borehole cutaway soil",hideFlags=HideFlags.DontSave};soil.color=new Color(.34f,.25f,.19f);soil.SetFloat("_Smoothness",.08f);
            grass=new Material(soil){name="Borehole ground surface"};grass.color=new Color(.19f,.27f,.09f);
            ground=new GameObject("3D cutaway ground");ground.transform.SetParent(transform,false);ground.transform.position=column.transform.position;
            void Block(string name,Vector3 position,Vector3 scale,Material material){var go=GameObject.CreatePrimitive(PrimitiveType.Cube);go.name=name;go.layer=column.renderingLayer;go.transform.SetParent(ground.transform,false);go.transform.localPosition=position;go.transform.localScale=scale;go.GetComponent<Renderer>().sharedMaterial=material;Destroy(go.GetComponent<Collider>());}
            Block("Exposed ground interior",new Vector3(0,-column.displayHeight*.5f,3.1f),new Vector3(20,column.displayHeight,6),soil);
            interior=ground.transform.Find("Exposed ground interior");
            Block("Ground surface",new Vector3(0,.05f,3.1f),new Vector3(20,.1f,6),grass);
        }
        public void SetWidth(float scale)
        {
            if(ground==null)return;ground.transform.localScale=new Vector3(scale,1,scale);ground.transform.position=column.transform.position+Vector3.up*SurfaceOffset;
            float height=column.displayHeight+SurfaceOffset;interior.localPosition=new Vector3(0,-height*.5f,3.1f);interior.localScale=new Vector3(20,height,6);
        }
        void OnDisable(){if(camera!=null)camera.clearFlags=previousFlags;if(box!=null)box.material=previousSky;if(ground!=null)Destroy(ground);if(addedBox&&box!=null)Destroy(box);if(soil!=null)Destroy(soil);if(grass!=null)Destroy(grass);if(sky!=null)Destroy(sky);}
    }
}
