using UnityEngine;
namespace Subsurface.Borehole
{
    public sealed class BoreholeCutaway:MonoBehaviour
    {
        BoreholeColumn column;Transform interior;
        public float SurfaceOffset=>column!=null&&column.Document!=null&&column.Document.TopFeet>0?3f:0f;
        GameObject ground;Material soil,grass;Camera camera;CameraClearFlags previousFlags;Color previousBackground;
        public void Initialize(BoreholeColumn column,Camera target)
        {
            if(ground!=null)return;var assets=BoreholeRenderAssets.Load();this.column=column;camera=target;previousFlags=camera.clearFlags;previousBackground=camera.backgroundColor;camera.clearFlags=CameraClearFlags.SolidColor;camera.backgroundColor=new Color32(135,206,235,255);
            soil=new Material(assets.soil){name="Borehole cutaway soil",hideFlags=HideFlags.DontSave};soil.color=new Color(.34f,.25f,.19f);soil.SetFloat("_Smoothness",.08f);
            grass=new Material(assets.ground){name="Borehole ground surface"};grass.color=new Color(.19f,.27f,.09f);
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
        void OnDisable(){if(camera!=null){camera.clearFlags=previousFlags;camera.backgroundColor=previousBackground;}if(ground!=null)Destroy(ground);if(soil!=null)Destroy(soil);if(grass!=null)Destroy(grass);}
    }
}
